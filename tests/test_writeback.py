"""Tests for steelreuse.writeback: per-element status reshaping for the pyRevit "Apply Matches"
button (donor: reused/available/quarantined/unmapped; demand: filled/partially_filled/unfilled/
non_steel)."""


from steelreuse.core.carbon import Passport
from steelreuse.core.ec3_checks import MemberDemand
from steelreuse.core.sections import ValidationReport
from steelreuse.match.optimize import Assignment, DemandSlot, MatchResult
from steelreuse.pipeline import PipelineResult, run_pipeline
from steelreuse.schema import ExtractedMember, ExtractedModel
from steelreuse.writeback import build_writeback


def test_writeback_statuses_end_to_end(tmp_path):
    donor = ExtractedModel(kind="donor", source="pyrevit", members=[
        ExtractedMember(id="D1", role="beam", category="Structural Framing",
                        raw_section="W Shapes W18x55", length_mm=7000),
        ExtractedMember(id="D2", role="beam", category="Structural Framing",
                        raw_section="W Shapes W18x55", length_mm=7000),
        ExtractedMember(id="D3", role="beam", category="Structural Framing",
                        raw_section="W Shapes W18x55", length_mm=7000,
                        condition_grade="D", verification_status="documented"),
        ExtractedMember(id="D4", role="column", category="Structural Columns",
                        raw_section="Concrete-Rectangular-Column CC24x24", length_mm=4000),
    ])
    demand = ExtractedModel(kind="demand", source="pyrevit", members=[
        ExtractedMember(id="N1", role="beam", category="Structural Framing",
                        raw_section="W Shapes W16x26", spans_mm=[6000]),
        ExtractedMember(id="N2", role="beam", category="Structural Framing",
                        raw_section="W Shapes W16x26", spans_mm=[25000]),
        ExtractedMember(id="N3", role="column", category="Structural Columns",
                        raw_section="Concrete-Rectangular-Column CC24x24", length_mm=4000),
    ])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)

    res = run_pipeline(str(dp), str(mp), steel_only_demand=True)
    wb = build_writeback(res)

    donor_status = {k: v["status"] for k, v in wb["donor"].items()}
    assert donor_status["D3"] == "quarantined"
    assert "condition D" in wb["donor"]["D3"]["note"]
    assert donor_status["D4"] == "unmapped"
    # exactly one of D1/D2 is reused (for N1's slot), the other is available
    assert sorted([donor_status["D1"], donor_status["D2"]]) == ["available", "reused"]

    demand_status = {k: v["status"] for k, v in wb["demand"].items()}
    assert demand_status["N1"] == "filled"
    assert "filled by reuse" in wb["demand"]["N1"]["note"]
    assert demand_status["N2"] == "unfilled"  # no donor is 25 m long
    assert demand_status["N3"] == "non_steel"  # concrete, excluded from steel-only slots

    # structured pairing fields: the reused donor points at N1's slot and vice versa
    reused_id = "D1" if donor_status["D1"] == "reused" else "D2"
    assert wb["donor"][reused_id]["paired_with"] == "N1#0"
    assert wb["donor"][reused_id]["co2_saved_kg"] > 0
    assert wb["demand"]["N1"]["paired_with"] == reused_id
    assert wb["demand"]["N1"]["co2_saved_kg"] == wb["donor"][reused_id]["co2_saved_kg"]
    assert wb["demand"]["N2"]["paired_with"] == ""
    assert wb["demand"]["N2"]["co2_saved_kg"] is None

    # the summary block relabels the pipeline's headline numbers for the pyRevit button to print
    s = wb["summary"]
    assert s["donor_counts"]["quarantined"] == 1
    assert s["donor_counts"]["reused"] == 1
    assert s["demand_counts"]["non_steel"] == 1
    assert s["n_reused"] == 1
    assert s["slot_count"] == 2  # N1#0 + N2#0 (N3 excluded as non-steel)
    assert s["co2_saved_kg"] > 0


def test_writeback_partially_filled_multi_span():
    demand = ExtractedModel(kind="demand", source="pyrevit", members=[
        ExtractedMember(id="N4", role="beam", category="Structural Framing",
                        raw_section="W Shapes W16x26", spans_mm=[6000, 6000]),
    ])
    d = MemberDemand(My_Ed=1.0, Vz_Ed=1.0, L=6000, compression_flange_restrained=True)
    slots = [
        DemandSlot(id="N4#0", member_id="N4", role="beam", required_length_mm=6000, demand=d),
        DemandSlot(id="N4#1", member_id="N4", role="beam", required_length_mm=6000, demand=d),
    ]
    assignment = Assignment(
        supply_id="D1", slot_id="N4#0", section="W16X26", utilization=0.5, status="OK",
        offcut_mm=0.0, co2_saved_kg=10.0, score=10.0,
    )
    match = MatchResult(
        assignments=[assignment], unmatched_slots=["N4#1"], unused_supply=[], solver_status="test",
    )
    res = PipelineResult(
        supply_count=1, slot_count=2,
        validation=ValidationReport(mapped=[], fuzzy=[], unknown=[]),
        passport=Passport(entries=[]), match=match,
        donor=ExtractedModel(kind="donor", members=[]), demand=demand, slots=slots,
    )

    wb = build_writeback(res)
    assert wb["demand"]["N4"]["status"] == "partially_filled"
    assert "1/2 spans filled" in wb["demand"]["N4"]["note"]
    assert wb["demand"]["N4"]["paired_with"] == "D1"
    assert wb["demand"]["N4"]["co2_saved_kg"] == 10.0
