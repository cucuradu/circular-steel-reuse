"""Phase 5 tests: MILP matching (known-answer) + end-to-end pipeline on the sample models."""

import dataclasses
from pathlib import Path

import pytest

from steelreuse.core.carbon import load_factors
from steelreuse.core.ec3_checks import MemberDemand
from steelreuse.core.forces import AnalyticBackend
from steelreuse.core.sections import load_catalog, resolve_members
from steelreuse.match.optimize import DemandSlot, SupplyItem, baseline_new_mass_kg, match
from steelreuse.pipeline import build_slots, run_pipeline
from steelreuse.schema import ExtractedMember, ExtractedModel

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


def test_us_pipeline_reads_imperial_model(tmp_path):
    # A US/AISC model (AISC type names, no material grade) must read end-to-end on the default merged
    # catalog: W-shapes map and default to A992, the concrete column is the only unknown (never
    # guessed), and reuse + CO2 are computed.
    donor = ExtractedModel(kind="donor", source="pyrevit", members=[
        ExtractedMember(id="D1", role="beam", category="Structural Framing",
                        raw_section="W Shapes W18x55", length_mm=7000),
        ExtractedMember(id="D2", role="beam", category="Structural Framing",
                        raw_section="W Shapes W18x55", length_mm=7000),
        ExtractedMember(id="D3", role="column", category="Structural Columns",
                        raw_section="Concrete-Rectangular-Column CC24x24", length_mm=4000),
    ])
    demand = ExtractedModel(kind="demand", source="pyrevit", members=[
        ExtractedMember(id="N1", role="beam", category="Structural Framing",
                        raw_section="W Shapes W16x26", spans_mm=[6000]),
    ])
    dp, mp = tmp_path / "donor.json", tmp_path / "demand.json"
    donor.save(dp)
    demand.save(mp)

    res = run_pipeline(str(dp), str(mp))   # no catalog arg -> load_default_catalog() (EU + US)
    assert len(res.validation.mapped) == 2                       # both W18x55 mapped
    assert len(res.validation.unknown) == 1                      # only the concrete column
    assert res.validation.unknown[0].raw == "Concrete-Rectangular-Column CC24x24"
    assert res.supply_count == 2                                 # concrete excluded from supply
    assert res.match.n_reused >= 1
    assert res.match.total_co2_saved_kg > 0


def test_build_slots_steel_only_drops_unmapped(cat):
    # Non-steel demand (concrete, joists) maps to no catalog section; steel_only must skip it so it
    # never becomes a slot we'd try to fill with reclaimed steel.
    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="S1", role="beam", raw_section="IPE300", spans_mm=[6000]),
        ExtractedMember(id="X1", role="beam",
                        raw_section="Concrete-Rectangular Beam CB24x24", spans_mm=[6000]),
    ])
    resolve_members(demand.members, cat)
    assert {s.member_id for s in build_slots(demand)} == {"S1", "X1"}            # default: keep all
    assert {s.member_id for s in build_slots(demand, steel_only=True)} == {"S1"}  # drop the concrete


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
    # Booked CO2 is the net figure the optimiser uses: avoided new minus the reuse process carbon
    # minus the default 5 kg connection-refabrication carbon (match()'s connection_penalty_kg).
    expected = base * f.a1a3 - donor_used * f.reuse_process - 5.0
    assert a.co2_saved_kg == pytest.approx(expected, abs=0.5)
    # the baseline is far lighter than the donor -> saved is well below the naive donor-mass figure
    assert base < donor_used
    assert a.co2_saved_kg < donor_used * f.saved_per_kg


def test_greedy_fallback_skips_net_negative_pairs():
    # The greedy fallback must mirror the MILP, which leaves a negative-score x_ij at 0: never book a
    # net-negative (carbon-losing) reuse just to fill a slot, even on a solver timeout. Feasible but
    # net-negative pairs do reach the cell list, so the guard has to drop them here too.
    from steelreuse.match.optimize import _Cell, _solve_greedy

    pos = _Cell(si=0, sj=0, utilization=0.5, status="OK", offcut_mm=10.0, co2_saved_kg=20.0, score=20.0)
    neg = _Cell(si=1, sj=1, utilization=0.9, status="OK", offcut_mm=10.0, co2_saved_kg=-3.0, score=-3.0)
    assert _solve_greedy([pos, neg], n_supply=2, n_slots=2) == [pos]


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


def test_assignment_carries_chi_lt_for_the_report(cat):
    # #5: assignments surface the LTB factor so the report can show it. A restrained beam uses
    # chi_LT = 1.0 but still reports what it would be unrestrained.
    slot = _beam_slot(6000, 20.0)   # _beam_slot sets compression_flange_restrained=True
    supply = [SupplyItem(id="s", section="IPE360", grade="S275", length_mm=7000)]
    a = match(supply, [slot], cat).assignments[0]
    assert a.chi_lt == 1.0
    assert a.chi_lt_if_free is not None and 0.0 < a.chi_lt_if_free <= 1.0


def test_baseline_stays_within_slot_standard(cat):
    # EU<->US leak: a US slot's avoided-new baseline must be the lightest adequate W-shape, not a
    # coincidentally-lighter IPE. Two sections with identical IPE300 geometry (so both pass the same
    # checks) differing only in mass + standard make the restriction observable purely by mass.
    ipe = cat["IPE300"]
    light_eu = dataclasses.replace(ipe, name="EU_LIGHT", mass_kgm=10.0, standard="EU")
    heavy_us = dataclasses.replace(ipe, name="US_HEAVY", mass_kgm=30.0, standard="US")
    mini = {"EU_LIGHT": light_eu, "US_HEAVY": heavy_us}

    eu_slot = _beam_slot(4000, 8.0)
    eu_slot.grade = "S355"
    eu_slot.design_section = "EU_LIGHT"
    us_slot = _beam_slot(4000, 8.0)
    us_slot.grade = "A992"
    us_slot.design_section = "US_HEAVY"
    # Without the standard filter both would pick EU_LIGHT (10 kg/m); with it each stays in its own.
    assert baseline_new_mass_kg(eu_slot, mini) == pytest.approx(10.0 * 4.0)   # 40 kg
    assert baseline_new_mass_kg(us_slot, mini) == pytest.approx(30.0 * 4.0)   # 120 kg
