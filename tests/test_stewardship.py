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
