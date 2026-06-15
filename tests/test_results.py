"""Tests for steelreuse.writeback.build_results: the assignment-keyed results.json contract that the
Revit dockable results panel consumes (distinct from build_writeback's element-keyed status.json).

Pure reshaping of values run_pipeline already computed -- no new arithmetic
(docs/DESIGN_PRINCIPLES.md hard rule 1).
"""

import json

from steelreuse.pipeline import run_pipeline
from steelreuse.schema import ExtractedMember, ExtractedModel
from steelreuse.writeback import build_results


def _run(tmp_path):
    """A small donor/demand pair with one clean reuse, one too-long (unfilled) slot, and one
    audit-quarantined donor -- exercises every section of the results contract."""
    donor = ExtractedModel(kind="donor", source="pyrevit", members=[
        ExtractedMember(id="D1", role="beam", category="Structural Framing",
                        raw_section="W Shapes W18x55", length_mm=7000),
        ExtractedMember(id="D2", role="beam", category="Structural Framing",
                        raw_section="W Shapes W18x55", length_mm=7000),
        ExtractedMember(id="D3", role="beam", category="Structural Framing",
                        raw_section="W Shapes W18x55", length_mm=7000,
                        condition_grade="D", verification_status="documented"),
    ])
    demand = ExtractedModel(kind="demand", source="pyrevit", members=[
        ExtractedMember(id="N1", role="beam", category="Structural Framing",
                        raw_section="W Shapes W16x26", spans_mm=[6000]),
        ExtractedMember(id="N2", role="beam", category="Structural Framing",
                        raw_section="W Shapes W16x26", spans_mm=[25000]),
    ])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)
    return run_pipeline(str(dp), str(mp), steel_only_demand=True)


def test_results_contract_is_json_safe_and_versioned(tmp_path):
    res = _run(tmp_path)
    out = build_results(res)

    # Must round-trip through JSON unchanged (no dataclasses/tuples leak into the contract).
    assert json.loads(json.dumps(out)) == out
    assert out["schema_version"] == 2
    assert set(out) >= {"schema_version", "kpis", "assignments", "unfilled", "quarantined_donors"}


def test_results_kpis_carry_the_full_header_set(tmp_path):
    kpis = build_results(_run(tmp_path))["kpis"]
    for key in ("slots", "reused", "co2_saved_kg", "objective", "proven_optimal",
                "supply_count", "mass_reused_kg", "distinct_sections",
                "reuse_rate_pct", "match_optimality"):
        assert key in kpis
    assert kpis["reused"] == 1
    assert kpis["mass_reused_kg"] > 0   # one W18X55 donor put back to work


def test_results_warnings_block_counts_are_present(tmp_path):
    warn = build_results(_run(tmp_path))["warnings"]
    for key in ("ltb_restraint_reliant", "imperfection_governed", "cut_donors",
                "reusable_remainder_m", "unknown", "unknown_breakdown", "connection_review"):
        assert key in warn
    assert isinstance(warn["unknown_breakdown"], list)


def test_results_diagnosis_is_present_dict(tmp_path):
    diag = build_results(_run(tmp_path))["diagnosis"]
    assert isinstance(diag, dict)   # may be {} when nothing is unfilled, but always a dict


def test_results_optional_blocks_absent_when_not_run(tmp_path):
    out = build_results(_run(tmp_path))   # no pareto / disposition / portfolio / audit-summary run
    assert "pareto" not in out
    assert "disposition" not in out
    assert "portfolio" not in out


def test_results_kpis_mirror_the_pipeline(tmp_path):
    res = _run(tmp_path)
    kpis = build_results(res)["kpis"]

    assert kpis["reused"] == res.match.n_reused == 1
    assert kpis["slots"] == res.slot_count
    assert kpis["objective"] == "co2"
    assert kpis["co2_saved_kg"] > 0
    assert isinstance(kpis["proven_optimal"], bool)


def test_results_assignment_row_separates_demand_from_donor(tmp_path):
    res = _run(tmp_path)
    rows = build_results(res)["assignments"]

    assert len(rows) == 1
    row = rows[0]
    # The slot wanted a W16X26 (demand) but is filled by a heavier W18X55 donor -- the row must
    # carry BOTH, since the panel shows demand and donor sections side by side.
    assert row["demand_id"] == "N1"
    assert row["slot_id"] == "N1#0"
    assert row["demand_section"] == "W16X26"
    assert row["donor_id"] in {"D1", "D2"}
    assert row["donor_section"] == "W18X55"
    assert 0.0 < row["utilization"] <= 1.0
    assert row["governing_combo"]
    assert row["co2_saved_kg"] > 0
    assert isinstance(row["connection_review"], bool)


def test_results_assignment_row_carries_check_diagnostics(tmp_path):
    res = _run(tmp_path)
    row = build_results(res)["assignments"][0]

    # The panel's Status column and the LTB restraint-reliance warning need these straight from the
    # Assignment (same values the HTML report shows), so the panel does no re-derivation.
    assert row["check_status"] in {"OK", "REVIEW"}
    assert "chi_lt" in row and "chi_lt_if_free" in row   # None or float, both keys always present
    assert isinstance(row["offcut_mm"], (int, float))


def test_results_lists_unfilled_demand_slots(tmp_path):
    res = _run(tmp_path)
    unfilled = build_results(res)["unfilled"]

    # N2 is 25 m -- no 7 m donor can fill it.
    ids = {r["demand_id"] for r in unfilled}
    assert "N2" in ids
    n2 = next(r for r in unfilled if r["demand_id"] == "N2")
    assert n2["slot_id"] == "N2#0"
    assert n2["demand_section"] == "W16X26"


def test_results_lists_quarantined_donors_with_reason(tmp_path):
    res = _run(tmp_path)
    quarantined = build_results(res)["quarantined_donors"]

    ids = {r["donor_id"] for r in quarantined}
    assert "D3" in ids
    d3 = next(r for r in quarantined if r["donor_id"] == "D3")
    assert "condition D" in d3["reason"]
    assert d3["donor_section"]
