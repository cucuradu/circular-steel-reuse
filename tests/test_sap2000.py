"""CI-runnable tests for the experimental SAP2000 frame backend (no SAP2000 needed).

These exercise the pure-Python parts: the scope guard (refuses non-gravity cases before any COM
connection), the force-extraction adapter (:class:`_SapMemberForces`), and the benchmark
table/%diff assembly. The OAPI glue itself and PyNite↔SAP2000 parity live in
``test_sap2000_parity.py`` (skipped unless SAP2000 is installed) — exactly the project's
"tests must not require SAP2000" rule (docs/DESIGN_PRINCIPLES.md).
"""

import pytest

from steelreuse.benchmark.sap2000_bench import (
    analytic_frame_result,
    build_comparison,
    canonical_two_bay_frame,
    comparison_summary,
    comparison_to_markdown,
    member_force_summary,
    run_benchmark,
)
from steelreuse.core.ec3_checks import MemberDemand
from steelreuse.core.frame import (
    FrameOptions,
    FrameResult,
    _envelope_moment,
    _governing_axial,
    _governing_shear,
)
from steelreuse.core.frame_sap2000 import _SapMemberForces, analyze_frame_sap2000
from steelreuse.core.loads import AreaLoadModel
from steelreuse.schema import ExtractedMember


def _col(cid, x, y, z0, z1, section="IPE300", grade="S275"):
    return ExtractedMember(id=cid, role="column", section=section, material_grade=grade,
                           start_xyz=[x, y, z0], end_xyz=[x, y, z1], length_mm=z1 - z0)


def _beam(bid, x0, x1, z, section="IPE300", grade="S275"):
    return ExtractedMember(id=bid, role="beam", section=section, material_grade=grade,
                           start_xyz=[x0, 0.0, z], end_xyz=[x1, 0.0, z], spans_mm=[x1 - x0])


def _portal():
    return [_col("c1", 0, 0, 0, 3000), _col("c2", 6000, 0, 0, 3000), _beam("b1", 0, 6000, 3000)]


def test_scope_guard_refuses_sway_without_touching_sap2000():
    # A sway imperfection request is out of the gravity-only scope: refuse with ok=False BEFORE any
    # COM connection (so this runs in CI with no SAP2000). The warning names the unsupported case,
    # distinct from a "SAP2000 unavailable" fallback.
    res = analyze_frame_sap2000(
        _portal(), AreaLoadModel(), {}, options=FrameOptions(notional_phi=0.005))
    assert res.ok is False
    assert res.demands_by_member == {}
    assert any("sway" in w.lower() or "not supported" in w.lower() for w in res.warnings)


def test_scope_guard_refuses_wind():
    res = analyze_frame_sap2000(
        _portal(), AreaLoadModel(), {}, options=FrameOptions(wind_kpa=0.5))
    assert res.ok is False
    assert any("wind" in w.lower() or "not supported" in w.lower() for w in res.warnings)


def test_scope_guard_refuses_seismic():
    res = analyze_frame_sap2000(
        _portal(), AreaLoadModel(), {}, options=FrameOptions(seismic_cs=0.1))
    assert res.ok is False
    assert any("seismic" in w.lower() or "not supported" in w.lower() for w in res.warnings)


# ---------------------------------------------------------------------------
# _SapMemberForces — sign/axis mapping (SAP2000 -> EN/PyNite convention)
# ---------------------------------------------------------------------------
# SAP2000 FrameForce per station: P (axial, TENSION-positive), M3 (major-axis moment),
# M2 (minor-axis moment), V2 (major-axis shear), V3 (minor-axis shear). The adapter exposes the
# same method surface as a PyNite member so frame.py's extraction helpers work unchanged.


def _sap_forces(combo, *, P, M3, M2=None, V2=None, V3=None):
    n = len(P)
    rec = {"P": P, "M3": M3,
           "M2": M2 if M2 is not None else [0.0] * n,
           "V2": V2 if V2 is not None else [0.0] * n,
           "V3": V3 if V3 is not None else [0.0] * n}
    return _SapMemberForces({combo: rec})


def test_adapter_axial_is_compression_positive():
    # SAP P = -100 kN (compression). EN/PyNite convention is compression-POSITIVE, so the governing
    # axial the checker sees must be +100 kN. This is the one sign-critical mapping.
    f = _sap_forces("ULS", P=[-100_000.0, -100_000.0], M3=[0.0, 0.0])
    assert _governing_axial(f, "ULS") == 100_000.0


def test_adapter_tension_stays_negative():
    f = _sap_forces("ULS", P=[40_000.0, 40_000.0], M3=[0.0, 0.0])
    assert _governing_axial(f, "ULS") == -40_000.0


def test_adapter_major_axis_moment_maps_to_My_magnitude():
    # A simply-supported beam's parabolic diagram sampled at 3 stations; peak |M3| = 50 kNm.
    f = _sap_forces("ULS", P=[0.0, 0.0, 0.0], M3=[0.0, 50e6, 0.0], M2=[0.0, 5e6, 0.0])
    assert _envelope_moment(f, "My", "ULS") == 50e6   # major axis <- M3
    assert _envelope_moment(f, "Mz", "ULS") == 5e6    # minor axis <- M2


def test_adapter_shear_magnitude():
    f = _sap_forces("ULS", P=[0.0, 0.0], M3=[0.0, 0.0], V2=[30_000.0, -30_000.0])
    assert _governing_shear(f, "ULS") == 30_000.0


# ---------------------------------------------------------------------------
# Benchmark comparison table (no SAP2000 needed)
# ---------------------------------------------------------------------------

def _result(demands_by_member):
    return FrameResult(
        demands_by_member=demands_by_member,
        node_count=0, member_count=len(demands_by_member), base_node_ids=[],
        skipped_member_ids=[], warnings=[], ok=True,
    )


def test_member_force_summary_takes_governing_magnitude_across_combos():
    res = _result({"b1": [
        ("ULS", MemberDemand(N_Ed=10_000.0, My_Ed=50e6, Vz_Ed=30_000.0, L=6000.0)),
        ("SLS", MemberDemand(N_Ed=-12_000.0, My_Ed=33e6, Vz_Ed=20_000.0, L=6000.0)),
    ]})
    summ = member_force_summary(res)
    # governing = max magnitude across combos, per component
    assert summ["b1"].N == 12_000.0
    assert summ["b1"].M == 50e6
    assert summ["b1"].V == 30_000.0


def test_build_comparison_computes_pct_diff_vs_reference():
    pynite = _result({"b1": [("ULS", MemberDemand(N_Ed=0.0, My_Ed=50e6, Vz_Ed=30_000.0, L=6000.0))]})
    sap = _result({"b1": [("ULS", MemberDemand(N_Ed=0.0, My_Ed=51e6, Vz_Ed=30_000.0, L=6000.0))]})
    rows = build_comparison({"pynite": pynite, "sap2000": sap}, reference="pynite")
    (row,) = rows
    assert row.member_id == "b1"
    assert row.by_solver["sap2000"].M == 51e6
    assert row.pct_diff["sap2000"]["M"] == pytest.approx(2.0)   # (51-50)/50*100
    assert row.pct_diff["sap2000"]["V"] == pytest.approx(0.0)


def test_comparison_to_markdown_renders_a_table_with_members_and_solvers():
    pynite = _result({"b1": [("ULS", MemberDemand(N_Ed=0.0, My_Ed=50e6, Vz_Ed=30_000.0, L=6000.0))]})
    sap = _result({"b1": [("ULS", MemberDemand(N_Ed=0.0, My_Ed=51e6, Vz_Ed=30_000.0, L=6000.0))]})
    md = comparison_to_markdown(build_comparison({"pynite": pynite, "sap2000": sap}))
    assert "b1" in md
    assert "|" in md and md.strip().startswith("|")   # a markdown table


# ---------------------------------------------------------------------------
# Connection + graceful fallback (runs in CI *because* SAP2000 is absent here)
# ---------------------------------------------------------------------------

def test_connect_raises_sap2000_unavailable_without_comtypes():
    from steelreuse.core._sap2000 import Sap2000Unavailable, connect_sap2000
    # comtypes (the [sap2000] extra) is not installed in CI, so connecting must raise the one clean
    # exception type rather than a raw ImportError/COM error.
    with pytest.raises(Sap2000Unavailable):
        connect_sap2000()


def test_analytic_frame_result_beam_is_closed_form_wL2_over_8():
    from steelreuse.core.loads import AreaLoadModel
    members = canonical_two_bay_frame()
    loads = AreaLoadModel()
    res = analytic_frame_result(members, loads)
    beam = next(m for m in members if m.role == "beam")
    L = beam.spans_mm[0]
    w = loads.factored_area_kpa() * loads.beam_tributary_width_m   # N/mm
    (_, d) = res.demands_by_member[beam.id][0]
    assert d.My_Ed == pytest.approx(w * L * L / 8.0)
    assert d.Vz_Ed == pytest.approx(w * L / 2.0)


def test_run_benchmark_writes_csv_and_markdown(tmp_path):
    # Runs analytic + PyNite for real here (PyNite is installed); SAP2000 is absent so its column
    # shows as unavailable. Proves the whole pipeline wires together end to end.
    out = run_benchmark(tmp_path)
    csv_path = tmp_path / "forces_compare.csv"
    md_path = tmp_path / "forces_compare.md"
    assert csv_path.exists() and md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert "B0" in md and "B1" in md        # both bays' beams present
    assert "pynite" in md                     # the reference solver column rendered
    assert "sap2000" in out.unavailable       # SAP2000 reported unavailable in this env


def test_comparison_summary_counts_within_tolerance_and_finds_worst():
    pynite = _result({
        "b1": [("ULS", MemberDemand(My_Ed=50e6, Vz_Ed=30_000.0, L=6000.0))],
        "b2": [("ULS", MemberDemand(My_Ed=100e6, Vz_Ed=10_000.0, L=6000.0))]})
    sap = _result({
        "b1": [("ULS", MemberDemand(My_Ed=50.5e6, Vz_Ed=30_000.0, L=6000.0))],   # +1% on M
        "b2": [("ULS", MemberDemand(My_Ed=120e6, Vz_Ed=10_000.0, L=6000.0))]})     # +20% on M
    rows = build_comparison({"pynite": pynite, "sap2000": sap})
    summ = comparison_summary(rows, reference="pynite", tol=2.0)["sap2000"]
    assert summ.n_within == summ.n_components - 1     # only b2.M is out of tolerance
    assert summ.worst[0].member_id == "b2"
    assert summ.worst[0].component == "M"
    assert summ.worst[0].pct == pytest.approx(20.0)


def test_run_benchmark_on_a_loaded_demand_model(tmp_path):
    # Feed a real extracted model (not the toy frame): solve PyNite + SAP2000 on the SAME split
    # topology so members line up one-to-one. Analytic is omitted (its un-split keys wouldn't align).
    pytest.importorskip("Pynite")
    from steelreuse.schema import ExtractedModel
    model = ExtractedModel(kind="demand", members=[
        _col("K0", 0, 0, 0, 3000), _col("K1", 6000, 0, 0, 3000), _beam("KB", 0, 6000, 3000)])
    path = tmp_path / "demand.json"
    model.save(path)

    run = run_benchmark(tmp_path, demand_path=str(path))
    assert run.solvers == ["pynite", "sap2000"]        # analytic omitted for loaded models
    md = (tmp_path / "forces_compare.md").read_text(encoding="utf-8")
    assert "KB" in md                                   # the real model's beam appears


def test_gravity_path_falls_back_when_sap2000_unavailable():
    # A perfectly connectable gravity frame: with SAP2000 absent the backend must fall back like a
    # missing PyNite (ok=False, every member routed to analytic), never raise.
    res = analyze_frame_sap2000(_portal(), AreaLoadModel(), {}, options=FrameOptions())
    assert res.ok is False
    assert res.demands_by_member == {}
    assert set(res.skipped_member_ids) == {"c1", "c2", "b1"}
    # the fallback reason must reflect that SAP2000 couldn't be reached — not a stub placeholder
    assert any("sap2000" in w.lower() for w in res.warnings)
    assert not any("not yet implemented" in w.lower() for w in res.warnings)


def test_run_pipeline_solver_sap2000_routes_and_falls_back(tmp_path):
    # The pipeline --solver sap2000 route must reach the SAP2000 backend and, when it's unavailable,
    # fall back to the analytic path without crashing (frame.ok is False, matching still runs).
    pytest.importorskip("Pynite")
    from steelreuse.pipeline import run_pipeline
    from steelreuse.schema import ExtractedModel

    demand = ExtractedModel(kind="demand", members=[
        _col("N_c1", 0, 0, 0, 3000), _col("N_c2", 6000, 0, 0, 3000), _beam("N_b1", 0, 6000, 3000)])
    donor = ExtractedModel(kind="donor", members=[
        ExtractedMember(id="D_b", role="beam", section="IPE330", material_grade="S275",
                        raw_section="IPE330", length_mm=6500),
        ExtractedMember(id="D_c1", role="column", section="IPE300", material_grade="S275",
                        raw_section="IPE300", length_mm=3300),
        ExtractedMember(id="D_c2", role="column", section="IPE300", material_grade="S275",
                        raw_section="IPE300", length_mm=3300)])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)

    res = run_pipeline(str(dp), str(mp), loads=AreaLoadModel(),
                       frame_analysis=True, solver="sap2000")
    assert res.frame is not None
    assert res.frame.ok is False                                  # SAP2000 absent -> fell back
    assert any("sap2000" in w.lower() for w in res.frame.warnings)
    assert res.match.n_reused >= 1                                 # pipeline still produced matches
