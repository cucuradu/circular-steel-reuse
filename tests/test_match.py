"""Phase 5 tests: MILP matching (known-answer) + end-to-end pipeline on the sample models."""

from pathlib import Path

import pytest

from steelreuse.core.ec3_checks import MemberDemand
from steelreuse.core.forces import AnalyticBackend
from steelreuse.core.sections import load_catalog
from steelreuse.match.optimize import DemandSlot, SupplyItem, match
from steelreuse.pipeline import run_pipeline

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
