"""Phase 5 tests: MILP matching (known-answer) + end-to-end pipeline on the sample models."""

from pathlib import Path

import pytest

from steelreuse.core.carbon import load_factors
from steelreuse.core.ec3_checks import MemberDemand
from steelreuse.core.forces import AnalyticBackend
from steelreuse.core.sections import load_catalog, resolve_members
from steelreuse.match.optimize import DemandSlot, SupplyItem, baseline_new_mass_kg, match
from steelreuse.pipeline import build_slots, run_pipeline
from steelreuse.schema import ExtractedModel

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def _beam_slot(span_mm, udl, slot_id="S0"):
    M, V = AnalyticBackend().beam_span_forces(span_mm, udl)
    d = MemberDemand(My_Ed=M, Vz_Ed=V, L=span_mm, compression_flange_restrained=True)
    return DemandSlot(id=slot_id, member_id="m", role="beam", required_length_mm=span_mm, demand=d)


def test_match_picks_only_feasible_supply(cat):
    # Slot: 6 m span, 20 kN/m -> M = 90 kNm. IPE200 fails/too short; IPE360 passes & is long enough.
    slot = _beam_slot(6000, 20.0)
    supply = [
        SupplyItem(id="weak", section="IPE200", grade="S235", length_mm=4000),
        SupplyItem(id="strong", section="IPE360", grade="S275", length_mm=7000),
    ]
    res = match(supply, [slot], cat)
    assert res.n_reused == 1
    a = res.assignments[0]
    assert a.supply_id == "strong" and a.section == "IPE360"
    assert a.utilization == pytest.approx(0.322, abs=0.02)
    assert a.co2_saved_kg > 0
    assert res.unused_supply == ["weak"]


def test_unmatched_when_nothing_long_enough(cat):
    slot = _beam_slot(10000, 10.0, slot_id="long")
    supply = [SupplyItem(id="short", section="IPE400", grade="S355", length_mm=6000)]
    res = match(supply, [slot], cat)
    assert res.n_reused == 0
    assert res.unmatched_slots == ["long"]


def test_each_supply_used_at_most_once(cat):
    slot_a = _beam_slot(5000, 10.0, "A")
    slot_b = _beam_slot(5000, 10.0, "B")
    # one suitable long member, two identical slots -> only one can be filled
    supply = [SupplyItem(id="only", section="IPE360", grade="S275", length_mm=6000)]
    res = match(supply, [slot_a, slot_b], cat)
    assert res.n_reused == 1
    assert len(res.unmatched_slots) == 1


def test_end_to_end_pipeline_on_samples(cat):
    res = run_pipeline(
        str(DATA / "samples" / "donor.json"),
        str(DATA / "samples" / "demand.json"),
        catalog=cat,
    )
    assert res.validation.summary()  # mapping ran
    assert len(res.validation.unknown) == 1            # the US section
    assert len(res.passport.entries) == 7              # mapped donor members
    assert res.match.n_reused >= 3                     # several reuses found
    assert res.match.total_co2_saved_kg > 0


def test_co2_saved_uses_avoided_new_baseline_not_donor_mass(cat):
    # A1: a hugely oversized donor in a small slot must book the carbon of the *right-sized new
    # member* (the baseline), not the donor's own mass.
    slot = _beam_slot(4000, 8.0)          # M = 16 kNm
    slot.grade = "S355"                   # design grade of the new build
    supply = [SupplyItem(id="big", section="IPE500", grade="S355", length_mm=4200)]
    res = match(supply, [slot], cat)
    assert res.n_reused == 1
    a = res.assignments[0]

    f = load_factors()["steel"]
    base = baseline_new_mass_kg(slot, cat, "S355")        # ~IPE160 over 4 m
    donor_used = cat["IPE500"].mass_kgm * 4000 / 1000.0
    expected = base * f.a1a3 - donor_used * f.reuse_process
    assert a.co2_saved_kg == pytest.approx(expected, abs=0.5)
    # the baseline is far lighter than the donor -> saved is well below the naive donor-mass figure
    assert base < donor_used
    assert a.co2_saved_kg < donor_used * f.saved_per_kg


def test_degenerate_member_does_not_crash(cat):
    # A4: a zero-length column/donor would divide by zero in the buckling check; the matcher must
    # skip such rows (reporting them as unmatched/unused) instead of aborting the whole run.
    good = _beam_slot(6000, 15.0, "good")
    bad = DemandSlot(id="bad", member_id="m", role="column", required_length_mm=0.0,
                     demand=MemberDemand(N_Ed=400e3, L=0.0))
    supply = [SupplyItem(id="s", section="IPE360", grade="S275", length_mm=7000),
              SupplyItem(id="z", section="IPE300", grade="S275", length_mm=0.0)]
    res = match(supply, [good, bad], cat)   # must not raise
    assert "bad" in res.unmatched_slots
    assert "z" in res.unused_supply


def test_slots_carry_design_grade_and_section(cat):
    # A6: demand sections are mapped so each slot carries its design grade/section for the baseline.
    demand = ExtractedModel.load(DATA / "samples" / "demand.json")
    resolve_members(demand.members, cat)
    by_member = {s.member_id: s for s in build_slots(demand)}
    assert by_member["N1"].grade == "S275"
    assert by_member["N1"].design_section == "IPE240"
