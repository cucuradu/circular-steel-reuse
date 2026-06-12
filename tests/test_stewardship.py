"""Stock stewardship & counterfactual fates (FUTURE_IMPROVEMENTS plan, 2026-06-12).

Phase A2: the stock disposition advisory (store / re-roll / recycle per unused donor).
Later phases (A1ii counterfactual booking, B* stewardship knobs, C* portfolio/scarcity) add their
tests here too, so the whole feature family lives in one place.
"""

import dataclasses
from pathlib import Path

import pytest

from steelreuse.core.carbon import load_factors
from steelreuse.core.ec3_checks import MemberDemand
from steelreuse.core.forces import AnalyticBackend
from steelreuse.core.sections import load_catalog
from steelreuse.match.optimize import (
    DemandSlot,
    SupplyItem,
    match,
    stock_disposition,
)
from steelreuse.pipeline import run_pipeline

DATA = Path(__file__).resolve().parents[1] / "src" / "steelreuse" / "data"


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def _beam_slot(span_mm, udl, slot_id="S0"):
    M, V = AnalyticBackend().beam_span_forces(span_mm, udl)
    d = MemberDemand(My_Ed=M, Vz_Ed=V, L=span_mm, compression_flange_restrained=True)
    return DemandSlot(id=slot_id, member_id="m", role="beam", required_length_mm=span_mm, demand=d)


# ---------------------------------------------------------------------------
# B1 — utilization floor (opt-in)
# ---------------------------------------------------------------------------

def test_min_util_refuses_a_grossly_overspec_donor(cat):
    # A lightly loaded 4 m slot (M = 16 kNm) and a massive IPE500 donor: accepted by default at a
    # tiny utilization, refused under --min-util 0.3 (the slot goes unfilled and the donor stays in
    # stock), and verify_match stays clean on the floored result because min_util travels on
    # weights. The disposition advisory on the floored run must NOT call it a store candidate
    # (the same floor applies to its re-derived cells).
    from steelreuse.match.optimize import verify_match

    slot = _beam_slot(4000, 8.0)
    supply = [SupplyItem(id="big", section="IPE500", grade="S355", length_mm=4200)]

    default = match(supply, [slot], cat)
    assert default.n_reused == 1
    assert default.assignments[0].utilization < 0.3   # grossly over-spec

    floored = match(supply, [slot], cat, min_util=0.3)
    assert floored.n_reused == 0
    assert floored.unmatched_slots == [slot.id] and floored.unused_supply == ["big"]
    assert floored.weights["min_util"] == 0.3
    assert verify_match(supply, [slot], cat, floored) == []

    rows = stock_disposition(supply, [slot], cat, floored)
    assert len(rows) == 1 and not rows[0]["feasible_for_unfilled"]
    assert rows[0]["advice"] in ("re-roll", "recycle")


def test_min_util_keeps_adequately_utilized_pairs(cat):
    # The floor must not reject a donor whose governing utilization clears it.
    slot = _beam_slot(6000, 20.0)
    supply = [SupplyItem(id="s", section="IPE360", grade="S275", length_mm=7000)]
    res = match(supply, [slot], cat, min_util=0.3)   # IPE360 sits at ~0.32 here
    assert res.n_reused == 1
    assert res.assignments[0].utilization >= 0.3


# ---------------------------------------------------------------------------
# B2 — over-spec soft penalty (opt-in)
# ---------------------------------------------------------------------------

def test_w_overspec_breaks_an_exact_tie_toward_the_lighter_donor(cat):
    # Two adequate donors of EQUAL length whose raw scores tie EXACTLY (a custom factor table with
    # reuse_process = 0 removes the existing mild light-donor preference; cutting mode removes the
    # off-cut term). With w_overspec = 0 the solver may pick either; with w_overspec > 0 the
    # lighter donor must win — and the booked CO2 of the assignment must be unchanged by the knob
    # (the penalty lives in the score only).
    from steelreuse.core.carbon import CarbonFactor

    flat = {"steel": CarbonFactor(a1a3=1.55, reuse_process=0.0)}
    slot = _beam_slot(6000, 20.0)
    supply = [SupplyItem(id="heavy", section="IPE550", grade="S275", length_mm=7000),
              SupplyItem(id="light", section="IPE360", grade="S275", length_mm=7000)]

    tie = match(supply, [slot], cat, factors=flat, allow_cutting=True)
    assert tie.n_reused == 1   # one slot -> one winner, whichever of the tied pair it is

    steered = match(supply, [slot], cat, factors=flat, allow_cutting=True, w_overspec=0.3)
    assert steered.n_reused == 1
    a = steered.assignments[0]
    assert a.supply_id == "light"
    # booked CO2 is NOT reduced by the penalty: identical to the no-knob booking for this donor
    light_only = match([supply[1]], [slot], cat, factors=flat, allow_cutting=True)
    assert a.co2_saved_kg == pytest.approx(light_only.assignments[0].co2_saved_kg, abs=0.01)
    # ...but the score is (the penalty is real): strictly below the booked saving
    assert a.score < a.co2_saved_kg
    assert steered.weights["w_overspec"] == 0.3


def test_w_overspec_flips_a_default_heavy_choice(cat):
    # One-piece mode, default economics: a short heavy donor beats a long light donor on raw score
    # (the light one drags a big off-cut penalty). The over-spec knob must flip the choice to the
    # lighter section — the "Frankenstein receiver" fix — and verify_match must stay clean because
    # w_overspec travels on weights.
    from steelreuse.match.optimize import verify_match

    slot = _beam_slot(4000, 8.0)
    supply = [SupplyItem(id="heavy", section="IPE500", grade="S355", length_mm=4100),
              SupplyItem(id="light", section="IPE240", grade="S355", length_mm=8000)]

    default = match(supply, [slot], cat)
    assert default.assignments[0].supply_id == "heavy"   # off-cut penalty buries the light donor

    steered = match(supply, [slot], cat, w_overspec=0.3)
    assert steered.n_reused == 1
    assert steered.assignments[0].supply_id == "light"
    assert verify_match(supply, [slot], cat, steered) == []


def test_w_overspec_default_keeps_results_identical(cat):
    # Explicit 0.0 must produce the exact same assignments and scores as not passing it at all.
    slot = _beam_slot(6000, 20.0)
    supply = [SupplyItem(id="a", section="IPE400", grade="S275", length_mm=7000),
              SupplyItem(id="b", section="IPE360", grade="S275", length_mm=6500)]
    base = match(supply, [slot], cat)
    explicit = match(supply, [slot], cat, w_overspec=0.0)
    assert [dataclasses.asdict(x) for x in base.assignments] == \
        [dataclasses.asdict(x) for x in explicit.assignments]


# ---------------------------------------------------------------------------
# A2 — stock disposition advisory
# ---------------------------------------------------------------------------

def test_disposition_known_answer_store_reroll_recycle(cat):
    # Three unused donors, three different best fates:
    #   * "fits"  — an IPE400 that passes the unfilled 6 m slot at a positive score -> store;
    #   * "long"  — a straight 8 m IPE200 that cannot carry the slot but clears the re-rolling
    #               minimum length -> re-roll (credit mass x reroll_credit > mass x recycle_credit);
    #   * "stub"  — a 1 m off-cut: feasible for nothing, too short to re-roll -> recycle.
    # The result is a post-solve stock review (the original assignment fell through), built by
    # emptying a real match result: in an unconstrained proven-optimal run a positive
    # (unused donor, unfilled slot) pair cannot exist — it would be an improving move.
    slot = _beam_slot(6000, 20.0, "S")
    supply = [
        SupplyItem(id="fits", section="IPE400", grade="S275", length_mm=7000),
        SupplyItem(id="long", section="IPE200", grade="S235", length_mm=8000),
        SupplyItem(id="stub", section="IPE300", grade="S275", length_mm=1000),
    ]
    real = match(supply, [slot], cat)
    assert real.assignments and real.assignments[0].supply_id == "fits"  # sanity: it would be used
    review = dataclasses.replace(
        real, assignments=[], unmatched_slots=["S"], unused_supply=[s.id for s in supply])

    rows = stock_disposition(supply, [slot], cat, review)
    by_id = {r["supply_id"]: r for r in rows}
    assert set(by_id) == {"fits", "long", "stub"}
    f = load_factors()["steel"]

    fits = by_id["fits"]
    assert fits["advice"] == "store"
    assert fits["feasible_for_unfilled"] and fits["store_slot"] == "S"
    assert fits["store_score_kg"] is not None and fits["store_score_kg"] > 0
    # the store score is exactly the score the matcher would have used for the pair
    assert fits["store_score_kg"] == real.assignments[0].score

    long_ = by_id["long"]
    assert long_["advice"] == "re-roll"
    assert not long_["feasible_for_unfilled"] and long_["store_score_kg"] is None
    assert long_["reroll_eligible"]
    mass_long = cat["IPE200"].mass_kgm * 8.0
    assert long_["reroll_credit_kg"] == pytest.approx(mass_long * f.reroll_credit, abs=0.01)
    assert long_["recycle_credit_kg"] == pytest.approx(mass_long * f.recycle_credit, abs=0.01)
    assert long_["reroll_credit_kg"] > long_["recycle_credit_kg"]

    stub = by_id["stub"]
    assert stub["advice"] == "recycle"
    assert not stub["reroll_eligible"] and stub["reroll_credit_kg"] == 0.0
    mass_stub = cat["IPE300"].mass_kgm * 1.0
    assert stub["recycle_credit_kg"] == pytest.approx(mass_stub * f.recycle_credit, abs=0.01)


# ---------------------------------------------------------------------------
# A1(ii) — counterfactual booking (opt-in)
# ---------------------------------------------------------------------------

def test_counterfactual_shifts_booking_by_exactly_mass_times_credit(cat):
    # A donor that stays net-positive under every basis: the booked saving must drop by exactly
    # mass_used x credit between bases, and verify_match must stay clean (the mode + credit travel
    # on weights).
    from steelreuse.match.optimize import verify_match

    slot = _beam_slot(4000, 8.0)
    supply = [SupplyItem(id="d", section="IPE240", grade="S355", length_mm=4200)]
    f = load_factors()["steel"]
    mass_used = cat["IPE240"].mass_kgm * 4.0

    base = match(supply, [slot], cat)
    rec = match(supply, [slot], cat, counterfactual="recycling")
    assert base.n_reused == rec.n_reused == 1
    shift = base.assignments[0].co2_saved_kg - rec.assignments[0].co2_saved_kg
    assert shift == pytest.approx(mass_used * f.recycle_credit, abs=0.02)
    # the score shifts identically (the same subtraction flows through)
    assert base.assignments[0].score - rec.assignments[0].score == pytest.approx(shift, abs=0.02)
    assert rec.weights["counterfactual"] == "recycling"
    assert rec.weights["counterfactual_credit"] == pytest.approx(f.recycle_credit)
    assert verify_match(supply, [slot], cat, rec) == []


def test_counterfactual_recycling_skips_a_pair_that_is_only_positive_gross(cat):
    # A heavily over-spec donor: net-positive under plain avoided-new, net-NEGATIVE once the
    # foregone recycling credit of all that consumed steel is charged -> the co2 objective must
    # leave the slot unfilled. Under 'rerolling' (a larger credit) it stays skipped too.
    slot = _beam_slot(4000, 8.0)
    supply = [SupplyItem(id="big", section="IPE500", grade="S355", length_mm=4200)]

    base = match(supply, [slot], cat)
    assert base.n_reused == 1 and base.assignments[0].co2_saved_kg > 0

    rec = match(supply, [slot], cat, counterfactual="recycling")
    assert rec.n_reused == 0
    assert rec.unmatched_slots == [slot.id] and rec.unused_supply == ["big"]

    rr = match(supply, [slot], cat, counterfactual="rerolling")
    assert rr.n_reused == 0


def test_counterfactual_unknown_mode_rejected(cat):
    with pytest.raises(ValueError, match="counterfactual"):
        match([], [], cat, counterfactual="landfill")


def test_counterfactual_flows_through_pipeline_and_report():
    res = run_pipeline(str(DATA / "samples" / "donor.json"), str(DATA / "samples" / "demand.json"),
                       counterfactual="recycling")
    assert res.match.weights["counterfactual"] == "recycling"
    base = run_pipeline(str(DATA / "samples" / "donor.json"), str(DATA / "samples" / "demand.json"))
    # the netted total can only be lower
    assert res.match.total_co2_saved_kg < base.match.total_co2_saved_kg

    from steelreuse.llm.report import build_report_context, render_html
    ctx = build_report_context(res)
    assert ctx["counterfactual"] == "recycling"
    assert "Carbon basis" in render_html(ctx, "n")
    assert "Carbon basis" not in render_html(build_report_context(base), "n")


def test_disposition_is_advisory_only_and_runs_through_the_pipeline():
    # run_pipeline(disposition=True) computes one row per unused donor without changing the match;
    # the default (off) leaves the field None.
    donor = str(DATA / "samples" / "donor.json")
    demand = str(DATA / "samples" / "demand.json")
    base = run_pipeline(donor, demand)
    assert base.disposition is None

    res = run_pipeline(donor, demand, disposition=True)
    assert res.disposition is not None
    assert len(res.disposition) == len(res.match.unused_supply)
    assert {r["supply_id"] for r in res.disposition} == set(res.match.unused_supply)
    assert all(r["advice"] in ("store", "re-roll", "recycle") for r in res.disposition)
    # advisory only: the match is identical with and without it
    assert [dataclasses.asdict(a) for a in res.match.assignments] == \
        [dataclasses.asdict(a) for a in base.match.assignments]
    # in a proven-optimal default run no unused donor can show a positive store score
    # (it would have been an improving move — verify_match clause 3)
    assert res.match.proven_optimal
    assert all(not (r["feasible_for_unfilled"] and r["store_score_kg"] > 0)
               for r in res.disposition)


def test_disposition_report_section_summarizes_by_section():
    from steelreuse.llm.report import build_report_context, render_html

    res = run_pipeline(str(DATA / "samples" / "donor.json"), str(DATA / "samples" / "demand.json"),
                       disposition=True)
    ctx = build_report_context(res)
    assert ctx["disposition_present"]
    totals = ctx["disposition_totals"]
    assert totals["n"] == len(res.disposition)
    assert totals["store"] + totals["reroll"] + totals["recycle"] == totals["n"]
    assert sum(r["n"] for r in ctx["disposition_by_section"]) == totals["n"]
    html = render_html(ctx, "narrative")
    assert "Stock disposition" in html

    # without the advisory the section is absent
    ctx_off = build_report_context(run_pipeline(
        str(DATA / "samples" / "donor.json"), str(DATA / "samples" / "demand.json")))
    assert not ctx_off["disposition_present"]
    assert "Stock disposition" not in render_html(ctx_off, "narrative")
