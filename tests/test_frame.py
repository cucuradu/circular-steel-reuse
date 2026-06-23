"""Tests for the real frame-analysis force source (:mod:`steelreuse.core.frame`).

Two layers:
  * topology (:func:`snap_nodes`) — pure Python, no solver needed;
  * the PyNite solve — guarded by ``importorskip`` so a base install without the ``[fea]`` extra still
    passes. The solve is validated two ways: it must **reproduce the closed-form simply-supported
    result** (``M = wL^2/8``, ``V = wL/2``) that the analytic backend gives for one bay, and it must
    **accumulate column axial down a multi-storey stack** the way hand statics says it should.
"""

import pytest

from steelreuse.core.forces import AnalyticBackend
from steelreuse.core.frame import (
    FrameOptions,
    analyze_frame,
    expand_spans,
    seismic_node_forces,
    snap_nodes,
    torsion_constant,
    wind_node_forces,
)
from steelreuse.core.loads import AreaLoadModel
from steelreuse.core.sections import load_default_catalog
from steelreuse.pipeline import LoadModel, run_pipeline
from steelreuse.schema import ExtractedMember, ExtractedModel


def _col(cid, x, y, z0, z1, section="IPE300", grade="S275"):
    return ExtractedMember(id=cid, role="column", section=section, material_grade=grade,
                           start_xyz=[x, y, z0], end_xyz=[x, y, z1], length_mm=z1 - z0)


def _beam(bid, x0, x1, z, section="IPE300", grade="S275"):
    return ExtractedMember(id=bid, role="beam", section=section, material_grade=grade,
                           start_xyz=[x0, 0.0, z], end_xyz=[x1, 0.0, z], spans_mm=[x1 - x0])


# ---------------------------------------------------------------------------
# Topology (no solver)
# ---------------------------------------------------------------------------

def test_snap_nodes_connects_shared_endpoints():
    # one-bay portal: the beam's ends must snap onto the two column tops -> 4 nodes, not 6.
    members = [_col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000),
               _beam("b1", 0, 6000, 3000)]
    topo = snap_nodes(members)
    assert len(topo.nodes) == 4
    assert topo.member_nodes["b1"][0] == topo.member_nodes["c1"][1]   # beam i == col1 top
    assert topo.member_nodes["b1"][1] == topo.member_nodes["c2"][1]   # beam j == col2 top
    assert len(topo.base_node_ids) == 2                              # the two column feet
    assert topo.skipped_member_ids == []


def test_snap_nodes_skips_members_without_geometry():
    members = [_col("c1", 0, 0, 0, 3000),
               ExtractedMember(id="nogeo", role="beam", spans_mm=[6000])]  # no xyz
    topo = snap_nodes(members)
    assert topo.skipped_member_ids == ["nogeo"]
    assert "nogeo" not in topo.member_nodes


def test_snap_nodes_supports_each_disconnected_component():
    # Two separate portals; the second is far away AND 5 m higher. Each must be supported at its OWN
    # lowest level, otherwise the higher piece floats and the whole solve is unstable.
    members = [
        _col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000), _beam("b1", 0, 6000, 3000),
        _col("c3", 0, 20000, 5000, 8000), _col("c4", 6000, 20000, 5000, 8000),
        ExtractedMember(id="b2", role="beam", section="IPE300", material_grade="S275",
                        start_xyz=[0, 20000, 8000], end_xyz=[6000, 20000, 8000], spans_mm=[6000]),
    ]
    topo = snap_nodes(members)
    base_z = {round(topo.nodes[b].z) for b in topo.base_node_ids}
    assert 0 in base_z and 5000 in base_z          # both pieces grounded at their own level


def test_stabilize_topology_prunes_hanging_member():
    from steelreuse.core.frame import _stabilize_topology
    # A valid portal plus a beam hanging off one column top into space (its far end connects to nothing
    # and neither end is a support): it must be pruned to analytic, the bay kept.
    members = [_col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000), _beam("b1", 0, 6000, 3000),
               _beam("hang", 6000, 12000, 3000)]
    topo = snap_nodes(members)
    pruned = _stabilize_topology(topo)
    assert "hang" in pruned and "hang" in topo.skipped_member_ids
    assert "b1" not in pruned and "b1" in topo.member_nodes


def test_snap_tolerance_merges_near_coincident_endpoints():
    # endpoints 40 mm apart (< 50 mm default tol) collapse to one node; 200 mm apart do not.
    members = [_col("c1", 0, 0, 0, 3000), _beam("b1", 40, 6000, 3000)]
    assert len(snap_nodes(members, snap_tol_mm=50.0).nodes) == 3       # merged
    assert len(snap_nodes(members, snap_tol_mm=10.0).nodes) == 4       # not merged


def test_torsion_constant_is_positive_and_small_vs_inertia():
    cat = load_default_catalog()
    j = torsion_constant(cat["IPE300"])
    assert 0 < j < cat["IPE300"].Iy            # open-section J << major-axis I


# ---------------------------------------------------------------------------
# Solve — validation
# ---------------------------------------------------------------------------

def test_portal_beam_recovers_simply_supported_closed_form():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    loads = AreaLoadModel(flange_restrained=True)  # slab-restrained floor beam; 9.225 kPa, 3 m trib
    members = [_col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000), _beam("b1", 0, 6000, 3000)]
    res = analyze_frame(members, loads, cat)
    assert res.ok and "b1" in res.demands_by_member

    name, dem = res.demands_by_member["b1"][0]
    assert name == "ULS gravity"
    # closed form for the same factored UDL the area model puts on a 3 m strip:
    w = loads.factored_area_kpa() * 3.0          # N/mm
    span = 6000.0
    m_analytic, v_analytic = AnalyticBackend().beam_span_forces(span, w)
    assert dem.My_Ed == pytest.approx(m_analytic, rel=1e-3)   # == w L^2 / 8
    assert dem.Vz_Ed == pytest.approx(v_analytic, rel=1e-3)   # == w L / 2
    assert dem.N_Ed == pytest.approx(0.0, abs=1.0)            # pinned beam carries ~no axial
    assert dem.compression_flange_restrained is True          # slab restraint (beam role)


def test_moment_shape_factors_come_from_the_solved_diagram():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    loads = AreaLoadModel()
    members = [_col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000), _beam("b1", 0, 6000, 3000)]

    off = analyze_frame(members, loads, cat)                          # conservative default
    assert off.demands_by_member["b1"][0][1].C1 == 1.0

    on = analyze_frame(members, loads, cat, options=FrameOptions(moment_shape=True))
    dem = on.demands_by_member["b1"][0][1]
    # The pinned beam's parabolic (simply-supported UDL) diagram, sampled from the real PyNite solve,
    # must give C1 ≈ 1.136 — proving the moment sampling actually ran (not the 1.0 fallback)...
    assert dem.C1 == pytest.approx(1.136, abs=2e-2)
    # ...and its ≈0 end moments give the conservative Cm = 1.0 (no end-moment reduction on a pin).
    assert dem.Cmy == pytest.approx(1.0, abs=1e-6)


def test_column_axial_accumulates_through_storeys():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    loads = AreaLoadModel()
    # single bay, two storeys: a continuous column line each side, a floor beam at each level.
    members = [
        _col("c1a", 0, 0, 0, 3000), _col("c1b", 0, 0, 3000, 6000),       # left column, 2 lifts
        _col("c2a", 6000, 0, 0, 3000), _col("c2b", 6000, 0, 3000, 6000), # right column, 2 lifts
        _beam("lvl1", 0, 6000, 3000), _beam("lvl2", 0, 6000, 6000),      # floor + roof beam
    ]
    res = analyze_frame(members, loads, cat)
    assert res.ok

    w = loads.factored_area_kpa() * 3.0
    half_floor = w * 6000.0 / 2.0                     # each beam delivers wL/2 to each column

    lower = res.demands_by_member["c1a"][0][1].N_Ed   # carries both floors
    upper = res.demands_by_member["c1b"][0][1].N_Ed   # carries the roof only
    assert upper == pytest.approx(half_floor, rel=1e-3)
    assert lower == pytest.approx(2 * half_floor, rel=1e-3)   # accumulation down the stack
    assert lower > 0                                          # compression-positive (EN sign)


def test_solve_failure_falls_back_gracefully():
    # no connectable geometry (members without coordinates) -> ok=False, everything skipped, no raise.
    loads = AreaLoadModel()
    members = [ExtractedMember(id="x", role="beam", spans_mm=[6000])]
    res = analyze_frame(members, loads, load_default_catalog())
    assert res.ok is False
    assert res.demands_by_member == {}
    assert res.skipped_member_ids == ["x"]
    assert res.warnings


# ---------------------------------------------------------------------------
# End-to-end pipeline with frame analysis on
# ---------------------------------------------------------------------------

def test_run_pipeline_with_frame_analysis(tmp_path):
    pytest.importorskip("Pynite")
    # A one-bay portal demand (real coordinates) matched against ample reclaimed stock.
    demand = ExtractedModel(kind="demand", members=[
        _col("N_c1", 0, 0, 0, 3000), _col("N_c2", 6000, 0, 0, 3000), _beam("N_b1", 0, 6000, 3000),
    ])
    donor = ExtractedModel(kind="donor", members=[
        ExtractedMember(id="D_b", role="beam", section="IPE330", material_grade="S275",
                        raw_section="IPE330", length_mm=6500),
        ExtractedMember(id="D_c1", role="column", section="IPE300", material_grade="S275",
                        raw_section="IPE300", length_mm=3300),
        ExtractedMember(id="D_c2", role="column", section="IPE300", material_grade="S275",
                        raw_section="IPE300", length_mm=3300),
    ])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)

    res = run_pipeline(str(dp), str(mp), loads=AreaLoadModel(), frame_analysis=True)
    assert res.frame is not None and res.frame.ok
    assert res.frame.node_count == 4 and res.frame.member_count == 3
    assert res.frame.skipped_member_ids == []
    assert res.slot_count == 3                 # one slot per solved element (no per-span split)
    assert res.match.n_reused >= 1
    assert res.match.total_co2_saved_kg > 0

    # frame_analysis without an explicit load model defaults to the area model (the CLI default)
    # instead of silently skipping the frame solve and answering from analytic forces.
    res_default = run_pipeline(str(dp), str(mp), frame_analysis=True)
    assert res_default.frame is not None and res_default.frame.ok
    assert res_default.match.n_reused == res.match.n_reused

    # ...and an explicit legacy flat LoadModel is refused (it has no floor pressure to distribute).
    with pytest.raises(ValueError, match="AreaLoadModel"):
        run_pipeline(str(dp), str(mp), loads=LoadModel(), frame_analysis=True)


# ---------------------------------------------------------------------------
# Lateral: notional sway (EHF) + P-Delta
# ---------------------------------------------------------------------------

def _braced_bay():
    # one bay with a diagonal brace from one column foot to the far column top.
    return [
        _col("c1", 0, 0, 0, 3500, section="HEB200"), _col("c2", 6000, 0, 0, 3500, section="HEB200"),
        _beam("bm", 0, 6000, 3500),
        ExtractedMember(id="br", role="brace", section="IPE200", material_grade="S275",
                        raw_section="IPE200", start_xyz=[0, 0, 0], end_xyz=[6000, 0, 3500]),
    ]


def test_notional_sway_adds_lateral_combos_and_engages_the_brace():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    res = analyze_frame(_braced_bay(), AreaLoadModel(), cat,
                        options=FrameOptions(notional_phi=1 / 200))
    assert res.ok
    # gravity + a sway combination in each lateral direction (EHF), and the solve was 2nd-order.
    names = [n for n, _ in res.demands_by_member["br"]]
    assert names == ["ULS gravity", "ULS gravity + sway X", "ULS gravity + sway Y"]
    assert any("sway imperfection (EHF)" in w for w in res.warnings)
    assert any("P-Delta" in w for w in res.warnings)
    # the brace picks up axial, and the in-plane sway case changes its force vs gravity alone.
    by_name = dict(res.demands_by_member["br"])
    assert abs(by_name["ULS gravity + sway X"].N_Ed) > 0
    assert by_name["ULS gravity + sway X"].N_Ed != pytest.approx(by_name["ULS gravity"].N_Ed)


def test_phi_zero_leaves_a_single_gravity_combination():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    members = [_col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000), _beam("b1", 0, 6000, 3000)]
    res = analyze_frame(members, AreaLoadModel(), cat, options=FrameOptions(notional_phi=0.0))
    assert [n for n, _ in res.demands_by_member["b1"]] == ["ULS gravity"]   # no extra DESIGN combo
    # phi=0 still runs the always-on alpha_cr probe: this bare portal is sway-sensitive, so k = 1.0 is
    # flagged (the probe measures stiffness, it is not a design action -> design combos unchanged above).
    assert any("alpha_cr" in w and "k = 1.0 system lengths NOT justified" in w for w in res.warnings)


def test_pdelta_option_runs_without_phi():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    loads = AreaLoadModel()
    members = [_col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000), _beam("b1", 0, 6000, 3000)]
    res = analyze_frame(members, loads, cat, options=FrameOptions(second_order=True))
    assert res.ok and any("P-Delta" in w for w in res.warnings)
    # no lateral load, so the 2nd-order beam moment is still essentially wL^2/8.
    w = loads.factored_area_kpa() * 3.0
    m_analytic, _ = AnalyticBackend().beam_span_forces(6000.0, w)
    assert res.demands_by_member["b1"][0][1].My_Ed == pytest.approx(m_analytic, rel=1e-2)


# ---------------------------------------------------------------------------
# Lateral: wind storey forces
# ---------------------------------------------------------------------------

def _box(L=6000.0, W=6000.0, H=3500.0, storeys=1):
    # a 1-bay x 1-bay 3-D box: 4 corner column lines + 4 perimeter beams (X and Y) per storey.
    corners = [(0, 0), (L, 0), (0, W), (L, W)]
    edges = [((0, 0), (L, 0)), ((0, W), (L, W)), ((0, 0), (0, W)), ((L, 0), (L, W))]
    members = []
    for k, (x, y) in enumerate(corners):
        for s in range(storeys):
            members.append(_col(f"c{k}_{s}", x, y, s * H, (s + 1) * H, section="HEB200"))
    for s in range(storeys):
        z = (s + 1) * H
        for e, ((x0, y0), (x1, y1)) in enumerate(edges):
            span = max(abs(x1 - x0), abs(y1 - y0))
            members.append(ExtractedMember(id=f"bm{e}_{s}", role="beam", section="IPE300",
                           material_grade="S275", raw_section="IPE300",
                           start_xyz=[x0, y0, z], end_xyz=[x1, y1, z], spans_mm=[span]))
    return members


def test_wind_node_forces_lumps_storey_shear_on_column_tops():
    topo = snap_nodes(_box(L=6000, W=6000, H=3500))
    members_by_id = {m.id: m for m in _box(L=6000, W=6000, H=3500)}
    forces = wind_node_forces(topo, members_by_id, wind_kpa=1.0, direction="FX")
    # q·width·h_trib = 1e-3 N/mm^2 · 6000 mm · (3500/2) mm = 10500 N, split over the 4 column tops.
    assert sum(forces.values()) == pytest.approx(10500.0, rel=1e-6)
    assert len(forces) == 4
    for f in forces.values():
        assert f == pytest.approx(2625.0, rel=1e-6)


def test_wind_skips_a_planar_frame():
    # a frame with no perpendicular plan extent (all y = 0) yields no wind in X (needs a 3-D model).
    members = [_col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000), _beam("b1", 0, 6000, 3000)]
    topo = snap_nodes(members)
    assert wind_node_forces(topo, {m.id: m for m in members}, 1.0, "FX") == {}


def test_wind_adds_combinations_and_loads_the_frame():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    res = analyze_frame(_box(), AreaLoadModel(), cat, options=FrameOptions(wind_kpa=0.8))
    assert res.ok
    names = [n for n, _ in res.demands_by_member["c0_0"]]
    assert "ULS gravity" in names
    assert "ULS gravity + wind X" in names and "ULS gravity + wind Y" in names
    assert any("wind 0.8 kN/m^2" in w for w in res.warnings)
    assert any("P-Delta" in w for w in res.warnings)
    # wind changes the windward/leeward column axial vs gravity alone.
    by_name = dict(res.demands_by_member["c0_0"])
    assert by_name["ULS gravity + wind X"].N_Ed != pytest.approx(by_name["ULS gravity"].N_Ed)


# ---------------------------------------------------------------------------
# Lateral: seismic (EN 1998-1 lateral force method)
# ---------------------------------------------------------------------------

def test_seismic_node_forces_distribute_as_inverted_triangle():
    # two equal-mass storeys: base shear = Cs·W_total, distributed F_i ∝ W_i·z_i -> roof = 2× floor.
    loads = AreaLoadModel()
    members = _box(storeys=2)
    topo = snap_nodes(members)
    cs, psi2 = 0.12, 0.3
    forces = seismic_node_forces(topo, {m.id: m for m in members}, loads, cs, psi2)
    # total applied force == base shear == Cs · seismic weight (Σ over beams of (g+ψ2 q)·trib·L)
    w_total = sum((loads.dead_kpa + psi2 * loads.live_kpa) * loads.beam_tributary_width_m * 6000.0
                  for m in members if m.role == "beam")
    assert sum(forces.values()) == pytest.approx(cs * w_total, rel=1e-6)
    # split the applied nodes by elevation: the roof level carries twice the first-floor level
    lower = sum(f for nid, f in forces.items() if topo.nodes[nid].z == pytest.approx(3500.0))
    upper = sum(f for nid, f in forces.items() if topo.nodes[nid].z == pytest.approx(7000.0))
    assert upper == pytest.approx(2 * lower, rel=1e-6)


def test_seismic_adds_a_unit_factor_design_situation():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    res = analyze_frame(_box(storeys=2), AreaLoadModel(), cat, options=FrameOptions(seismic_cs=0.15))
    assert res.ok
    names = [n for n, _ in res.demands_by_member["c0_0"]]
    assert "seismic X" in names and "seismic Y" in names
    assert any("seismic" in w and "Cs=0.15" in w for w in res.warnings)
    assert any("P-Delta" in w for w in res.warnings)
    by_name = dict(res.demands_by_member["c0_0"])
    assert by_name["seismic X"].N_Ed != pytest.approx(by_name["ULS gravity"].N_Ed)


# ---------------------------------------------------------------------------
# Continuous (multi-span) members
# ---------------------------------------------------------------------------

def _continuous_beam(bid="B", x0=0.0, x1=12000.0, z=3000.0, spans=(6000.0, 6000.0)):
    return ExtractedMember(id=bid, role="beam", section="IPE300", material_grade="S275",
                           raw_section="IPE300", start_xyz=[x0, 0.0, z], end_xyz=[x1, 0.0, z],
                           spans_mm=list(spans))


def test_expand_spans_splits_a_continuous_beam():
    out = expand_spans([_continuous_beam()])
    assert [x.id for x in out] == ["B#0", "B#1"]
    assert out[0].start_xyz == [0.0, 0.0, 3000.0] and out[0].end_xyz == [6000.0, 0.0, 3000.0]
    assert out[1].start_xyz == [6000.0, 0.0, 3000.0] and out[1].end_xyz == [12000.0, 0.0, 3000.0]
    assert out[0].spans_mm == [6000.0] and out[0].length_mm == 6000.0
    # a single-span beam and a column pass through untouched
    passthrough = expand_spans([_beam("S", 0, 6000, 0), _col("c", 0, 0, 0, 3000)])
    assert [x.id for x in passthrough] == ["S", "c"]


def test_continuous_beam_loads_the_interior_column():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    loads = AreaLoadModel()
    members = [_col("c0", 0, 0, 0, 3000), _col("cm", 6000, 0, 0, 3000), _col("c2", 12000, 0, 0, 3000),
               _continuous_beam()]
    res = analyze_frame(members, loads, cat)
    assert res.ok
    assert "B#0" in res.demands_by_member and "B#1" in res.demands_by_member
    # each span is checked over its own 6 m length, not the full 12 m
    w = loads.factored_area_kpa() * 3.0
    m_an, _ = AnalyticBackend().beam_span_forces(6000.0, w)
    assert res.demands_by_member["B#0"][0][1].My_Ed == pytest.approx(m_an, rel=1e-3)
    # the interior column now collects the reaction from BOTH spans (~2x an end column)
    end = res.demands_by_member["c0"][0][1].N_Ed
    mid = res.demands_by_member["cm"][0][1].N_Ed
    assert end > 0
    assert mid == pytest.approx(2 * end, rel=1e-3)


def test_run_pipeline_frame_splits_multispan_into_slots(tmp_path):
    pytest.importorskip("Pynite")
    demand = ExtractedModel(kind="demand", members=[
        _col("c0", 0, 0, 0, 3000), _col("cm", 6000, 0, 0, 3000), _col("c2", 12000, 0, 0, 3000),
        _continuous_beam(),
    ])
    donor = ExtractedModel(kind="donor", members=(
        [ExtractedMember(id=f"DB{i}", role="beam", section="IPE360", material_grade="S275",
                         raw_section="IPE360", length_mm=6500) for i in range(2)]
        + [ExtractedMember(id=f"DC{i}", role="column", section="IPE300", material_grade="S275",
                           raw_section="IPE300", length_mm=3300) for i in range(3)]))
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)

    res = run_pipeline(str(dp), str(mp), loads=AreaLoadModel(), frame_analysis=True)
    assert res.frame is not None and res.frame.ok
    # 3 columns + the continuous beam split into 2 spans = 5 slots (not one 12 m slot)
    assert res.slot_count == 5
    assert res.match.n_reused >= 1


def test_split_columns_at_framing_inserts_a_node_at_the_floor():
    from steelreuse.core.frame import split_columns_at_framing
    # a single full-height column with a beam framing into its mid-height: it must split there.
    members = [_col("C", 0, 0, 0, 6000), _beam("b", 0, 6000, 3000)]
    out = split_columns_at_framing(members)
    col_ids = [m.id for m in out if m.role == "column"]
    assert col_ids == ["C@0", "C@1"]
    seg = {m.id: (m.start_xyz[2], m.end_xyz[2]) for m in out if m.role == "column"}
    assert seg["C@0"] == (0, 3000) and seg["C@1"] == (3000, 6000)
    # a column with nothing framing into its interior passes through untouched.
    plain = split_columns_at_framing([_col("P", 0, 0, 0, 3000), _beam("t", 0, 6000, 3000)])
    assert [m.id for m in plain if m.role == "column"] == ["P"]


def test_full_height_column_picks_up_an_intermediate_floor():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    loads = AreaLoadModel()
    # one bay, two floors, but each column modelled as a SINGLE full-height element (the common Revit
    # pattern). The mid-floor beam must connect (column auto-split) and the column carry both floors.
    members = [
        _col("c1", 0, 0, 0, 6000), _col("c2", 6000, 0, 0, 6000),         # full-height columns
        _beam("lvl1", 0, 6000, 3000), _beam("lvl2", 0, 6000, 6000),      # floor + roof beams
    ]
    res = analyze_frame(members, loads, cat)
    assert res.ok
    w = loads.factored_area_kpa() * 3.0
    half_floor = w * 6000.0 / 2.0
    # the split column folds back into ONE slot over its full height, carrying both floors' reactions.
    slots = res.slots_by_member["c1"]
    assert len(slots) == 1
    assert slots[0].required_length_mm == pytest.approx(6000.0, rel=1e-6)
    assert slots[0].demands[0][1].N_Ed == pytest.approx(2 * half_floor, rel=1e-3)


def test_girder_stays_continuous_at_a_secondary_beam_crossing():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    loads = AreaLoadModel()
    # A girder spanning column-to-column whose spans_mm records a SECONDARY-beam crossing (no column at
    # midspan). The old per-span-pinned model made a vertical mechanism there; the girder must instead
    # stay moment-continuous, support the secondary, solve, and map to ONE reused slot (not two).
    girder = ExtractedMember(id="G", role="beam", section="IPE400", material_grade="S275",
                             raw_section="IPE400", start_xyz=[0, 0, 3000], end_xyz=[12000, 0, 3000],
                             spans_mm=[6000, 6000])
    secondary = ExtractedMember(id="S", role="beam", section="IPE240", material_grade="S275",
                                raw_section="IPE240", start_xyz=[6000, 0, 3000],
                                end_xyz=[6000, 6000, 3000], spans_mm=[6000])
    members = [
        _col("cL", 0, 0, 0, 3000), _col("cR", 12000, 0, 0, 3000),    # girder end columns
        _col("cS", 6000, 6000, 0, 3000),                            # supports the secondary's far end
        girder, secondary,
    ]
    res = analyze_frame(members, loads, cat)
    assert res.ok                                   # no vertical mechanism at the crossing
    assert len(res.slots_by_member["G"]) == 1       # continuous girder -> a single reused member
    assert res.slots_by_member["G"][0].required_length_mm == pytest.approx(12000.0, rel=1e-3)
    assert len(res.slots_by_member["S"]) == 1


def test_run_pipeline_frame_analysis_falls_back_without_geometry(tmp_path):
    # demand members without coordinates: the frame can't build, pipeline still runs (analytic path).
    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="N1", role="beam", section="IPE300", material_grade="S275",
                        raw_section="IPE300", spans_mm=[6000]),
    ])
    donor = ExtractedModel(kind="donor", members=[
        ExtractedMember(id="D1", role="beam", section="IPE360", material_grade="S275",
                        raw_section="IPE360", length_mm=6500),
    ])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)

    res = run_pipeline(str(dp), str(mp), loads=AreaLoadModel(), frame_analysis=True)
    assert res.frame is not None and res.frame.ok is False     # nothing connectable
    assert res.slot_count == 1                                 # analytic per-span slot still made
    assert res.match.n_reused == 1


def test_sway_cases_feed_both_moment_axes_to_the_checker():
    # Columns under the two orthogonal EHF directions bend about both local axes; both must reach
    # MemberDemand (My_Ed *and* Mz_Ed) instead of being collapsed to one worst-axis magnitude.
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    res = analyze_frame(_braced_bay(), AreaLoadModel(), cat,
                        options=FrameOptions(notional_phi=1 / 200))
    assert res.ok
    col = dict(res.demands_by_member["c1"])
    sway = [col["ULS gravity + sway X"], col["ULS gravity + sway Y"]]
    assert any(d.My_Ed > 1e3 for d in sway)       # N*mm; in-plane bending present
    assert any(d.Mz_Ed > 1e3 for d in sway)       # out-of-plane bending no longer discarded


# ---------------------------------------------------------------------------
# Sway-stiffness classification (alpha_cr, EN 1993-1-1 5.2.1(4)B)
# ---------------------------------------------------------------------------

def test_sway_alpha_cr_hand_arithmetic():
    from steelreuse.core.frame import sway_alpha_cr
    # one storey: h=3000, H=10 kN, V=1000 kN, drift=1.5 mm -> (10/1000)*(3000/1.5) = 20
    assert sway_alpha_cr([(3000, 10e3, 1000e3, 1.5)]) == pytest.approx(20.0)
    # minimum over storeys governs
    assert sway_alpha_cr([(3000, 10e3, 1000e3, 1.5),
                          (3000, 10e3, 500e3, 4.0)]) == pytest.approx(15.0)
    # unassessable storeys (no drift / no vertical load / no H) are skipped; empty -> None
    assert sway_alpha_cr([(3000, 10e3, 1000e3, 0.0)]) is None
    assert sway_alpha_cr([(3000, 10e3, 0.0, 1.5)]) is None
    assert sway_alpha_cr([]) is None


def test_alpha_cr_flags_a_floppy_portal_and_rewards_bracing():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    # Floppy: two slender 6 m IPE160 cantilever-ish columns (pinned-top beams), fixed bases only.
    floppy = [
        _col("c1", 0, 0, 0, 6000, section="IPE160"), _col("c2", 6000, 0, 0, 6000, section="IPE160"),
        _beam("bm", 0, 6000, 6000),
    ]
    res_f = analyze_frame(floppy, AreaLoadModel(), cat, options=FrameOptions(notional_phi=1 / 200))
    assert res_f.ok and res_f.alpha_cr                       # computed in at least one direction
    assert all(a > 0 for a in res_f.alpha_cr.values())
    assert min(res_f.alpha_cr.values()) < 10.0               # slender portal is sway-sensitive
    assert any("sway-sensitive" in w.lower() for w in res_f.warnings)

    # Same bay with a vertical brace: the braced direction must be far stiffer.
    res_b = analyze_frame(_braced_bay(), AreaLoadModel(), cat,
                          options=FrameOptions(notional_phi=1 / 200))
    assert res_b.ok and res_b.alpha_cr
    assert max(res_b.alpha_cr.values()) > min(res_f.alpha_cr.values())


def test_per_member_k_override_reaches_the_frame_demands():
    pytest.importorskip("Pynite")
    cat = load_default_catalog()
    members = [_col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000), _beam("b1", 0, 6000, 3000)]
    members[0].kz = 2.0                                       # engineer's judgment: sway about z
    res = analyze_frame(members, AreaLoadModel(), cat)
    d = res.demands_by_member["c1"][0][1]
    assert d.kz == 2.0 and d.ky == 1.0
    assert res.demands_by_member["c2"][0][1].kz == 1.0        # untouched member keeps the default
