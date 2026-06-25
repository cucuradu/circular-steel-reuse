"""Stock stewardship & counterfactual fates (stock stewardship & counterfactual fates).

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
# C1 — portfolio matching (multiple demand models, one donor stock)
# ---------------------------------------------------------------------------

def _save_model(tmp_path, name, members, kind="demand"):
    from steelreuse.schema import ExtractedMember, ExtractedModel
    model = ExtractedModel(kind=kind, members=[ExtractedMember(**m) for m in members])
    p = tmp_path / name
    model.save(p)
    return str(p)


def test_portfolio_donor_goes_to_the_project_where_it_saves_more(tmp_path):
    # Two single-slot projects compete for ONE good donor (no cutting: 7 m donor, both slots need
    # most of it). Project B's span is longer -> bigger avoided-new baseline -> more CO2 saved
    # there, so the combined optimization must allocate the donor to B and leave A unfilled.
    donor = _save_model(tmp_path, "donor.json", [
        {"id": "D1", "role": "beam", "raw_section": "IPE360",
         "material_grade": "S275", "length_mm": 7000},
    ], kind="donor")
    proj_a = _save_model(tmp_path, "proj_a.json", [
        {"id": "A1", "role": "beam", "raw_section": "IPE300",
         "material_grade": "S275", "spans_mm": [4000]},
    ])
    proj_b = _save_model(tmp_path, "proj_b.json", [
        {"id": "B1", "role": "beam", "raw_section": "IPE360",
         "material_grade": "S275", "spans_mm": [6000]},
    ])

    res = run_pipeline(donor, [proj_a, proj_b], allow_cutting=False)
    assert res.slot_count == 2
    assert res.match.n_reused == 1
    a = res.match.assignments[0]
    assert a.slot_id == "proj_b::B1#0"          # the donor went where it saves more
    assert "proj_a::A1#0" in res.match.unmatched_slots

    # per-project breakdown
    assert res.projects is not None and [p["tag"] for p in res.projects] == ["proj_a", "proj_b"]
    by_tag = {p["tag"]: p for p in res.projects}
    assert by_tag["proj_a"]["n_reused"] == 0 and by_tag["proj_a"]["n_unmatched"] == 1
    assert by_tag["proj_b"]["n_reused"] == 1 and by_tag["proj_b"]["n_unmatched"] == 0
    assert by_tag["proj_b"]["co2_saved_kg"] == pytest.approx(a.co2_saved_kg, abs=0.1)

    # sanity: alone, project A would happily take the donor — only the portfolio view says no
    solo = run_pipeline(donor, proj_a, allow_cutting=False)
    assert solo.match.n_reused == 1


def test_portfolio_single_demand_path_is_unchanged(tmp_path):
    # A single path (str) and a one-element list must both behave exactly like the historical
    # single-demand run: no namespacing, projects None.
    donor = str(DATA / "samples" / "donor.json")
    demand = str(DATA / "samples" / "demand.json")
    res_str = run_pipeline(donor, demand)
    res_list = run_pipeline(donor, [demand])
    assert res_str.projects is None and res_list.projects is None
    assert all("::" not in s.id for s in res_str.slots)
    assert [dataclasses.asdict(a) for a in res_str.match.assignments] == \
        [dataclasses.asdict(a) for a in res_list.match.assignments]


def test_portfolio_duplicate_stems_get_unique_tags(tmp_path):
    # Two demand models with the same file name must not collide in the namespace.
    donor = _save_model(tmp_path, "donor.json", [
        {"id": "D1", "role": "beam", "raw_section": "IPE360",
         "material_grade": "S275", "length_mm": 7000},
    ], kind="donor")
    sub = tmp_path / "sub"
    sub.mkdir()
    m1 = _save_model(tmp_path, "site.json", [
        {"id": "X1", "role": "beam", "raw_section": "IPE300",
         "material_grade": "S275", "spans_mm": [4000]},
    ])
    m2 = _save_model(sub, "site.json", [
        {"id": "X1", "role": "beam", "raw_section": "IPE300",
         "material_grade": "S275", "spans_mm": [5000]},
    ])
    res = run_pipeline(donor, [m1, m2])
    tags = [p["tag"] for p in res.projects]
    assert tags == ["site", "site-2"]
    assert {s.id for s in res.slots} == {"site::X1#0", "site-2::X1#0"}

    # the report context carries the per-project rows
    from steelreuse.llm.report import build_report_context, render_html
    ctx = build_report_context(res)
    assert [p["tag"] for p in ctx["projects"]] == ["site", "site-2"]
    assert "Portfolio" in render_html(ctx, "n")


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
# B3 — section-variety cap (opt-in, anti-Frankenstein)
# ---------------------------------------------------------------------------

def test_max_distinct_sections_forces_consolidation(cat):
    # Three identical 4 m slots. Unlimited (cutting mode), the proven optimum uses three different
    # cheap single-piece donors -> 3 distinct sections. Capped at 2, the solver must consolidate:
    # one cheap donor + the long heavy donor cut into two pieces (2 sections, all slots still
    # filled) at a PROVEN lower objective. verify_match must pass the capped result (a free donor
    # of a third family offering a better score is NOT an improving move under a saturated cap).
    from steelreuse.match.optimize import verify_match

    slots = [_beam_slot(4000, 8.0, f"S{i}") for i in range(3)]
    supply = [
        SupplyItem(id="big", section="IPE600", grade="S355", length_mm=9000),   # 2 pieces max
        SupplyItem(id="c300", section="IPE300", grade="S355", length_mm=4200),
        SupplyItem(id="c360", section="IPE360", grade="S355", length_mm=4200),
        SupplyItem(id="c400", section="IPE400", grade="S355", length_mm=4200),
    ]

    free = match(supply, slots, cat, allow_cutting=True)
    assert free.proven_optimal and free.n_reused == 3
    assert len({a.section for a in free.assignments}) == 3      # the three cheap singles
    assert "big" in free.unused_supply

    capped = match(supply, slots, cat, allow_cutting=True, max_distinct_sections=2)
    assert capped.proven_optimal and capped.n_reused == 3       # still fills everything
    assert len({a.section for a in capped.assignments}) <= 2
    # consolidation costs carbon — the cap is a buildability trade, and the numbers say how much
    assert capped.total_co2_saved_kg < free.total_co2_saved_kg
    assert capped.weights["max_distinct_sections"] == 2
    assert verify_match(supply, slots, cat, capped) == []

    # the displaced cheap donors are exactly the disposition advisory's audience now
    rows = stock_disposition(supply, slots, cat, capped)
    assert {r["supply_id"] for r in rows} == set(capped.unused_supply)


def test_max_distinct_sections_greedy_respects_the_cap():
    # The greedy fallback must refuse to open an (N+1)-th family even when a third-family cell
    # scores higher than anything else left.
    from steelreuse.match.optimize import _Cell, _solve_greedy

    cells = [
        _Cell(si=0, sj=0, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=10, score=10.0,
              used_len_mm=4000.0),
        _Cell(si=1, sj=1, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=9, score=9.0,
              used_len_mm=4000.0),
        _Cell(si=2, sj=2, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=8, score=8.0,
              used_len_mm=4000.0),
        _Cell(si=0, sj=2, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=2, score=2.0,
              used_len_mm=4000.0),
    ]
    fams = ["A", "B", "C"]
    caps = [9000.0, 9000.0, 9000.0]   # cutting mode, so the family-A donor can serve two slots
    unlimited = _solve_greedy(cells, n_supply=3, n_slots=3, caps=caps,
                              families=fams, max_families=None)
    assert {(c.si, c.sj) for c in unlimited} == {(0, 0), (1, 1), (2, 2)}
    capped = _solve_greedy(cells, n_supply=3, n_slots=3, caps=caps,
                           families=fams, max_families=2)
    # family C is never opened; slot 2 falls back to the lower-scoring family-A cell
    assert {(c.si, c.sj) for c in capped} == {(0, 0), (1, 1), (0, 2)}


def test_max_distinct_sections_validation_and_verify_flags_violation(cat):
    with pytest.raises(ValueError, match="max_distinct_sections"):
        match([], [], cat, max_distinct_sections=0)

    # a tampered result that uses more sections than its own cap must be flagged
    from steelreuse.match.optimize import verify_match
    slots = [_beam_slot(6000, 20.0, "s1"), _beam_slot(6000, 15.0, "s2")]
    supply = [SupplyItem(id="d1", section="IPE360", grade="S275", length_mm=7000),
              SupplyItem(id="d2", section="IPE400", grade="S275", length_mm=7000)]
    res = match(supply, slots, cat)
    assert len({a.section for a in res.assignments}) == 2
    tampered = dataclasses.replace(res, weights=dict(res.weights, max_distinct_sections=1))
    assert any("section-variety cap violated" in i
               for i in verify_match(supply, slots, cat, tampered))


# ---------------------------------------------------------------------------
# C2 — scarcity / option-value reserve weight (opt-in, EXPERIMENTAL)
# ---------------------------------------------------------------------------

def test_reserve_holds_a_scarce_heavy_donor_back_from_a_shared_slot(cat):
    # One light 4 m slot that two families can serve. Default (one-piece) economics: the SHORT heavy
    # IPE500 beats the LONG light IPE240 on score because its off-cut is tiny (same dynamic as
    # test_w_overspec_flips_a_default_heavy_choice), so at reserve_w = 0 the solver SPENDS the scarce
    # heavy donor on the shared slot. IPE500 is the scarce family (one donor, the only stock that
    # could carry a demanding slot a thin section cannot); IPE240 is abundant (two donors). With
    # reserve_w > 0 the scarce family is penalised on the shared slot — one an abundant donor could
    # also fill — and held back in stock; the abundant IPE240 takes the slot instead.
    from steelreuse.match.optimize import verify_match

    slot = _beam_slot(4000, 8.0)
    supply = [SupplyItem(id="rare", section="IPE500", grade="S355", length_mm=4100),
              SupplyItem(id="abund1", section="IPE240", grade="S355", length_mm=8000),
              SupplyItem(id="abund2", section="IPE240", grade="S355", length_mm=8000)]

    spent = match(supply, [slot], cat)                       # reserve off
    assert spent.n_reused == 1
    assert spent.assignments[0].supply_id == "rare"          # heavy donor spent on the shared slot

    held = match(supply, [slot], cat, reserve_w=0.2)         # reserve on
    assert held.n_reused == 1
    assert held.assignments[0].supply_id in ("abund1", "abund2")  # abundant donor used instead
    assert "rare" in held.unused_supply                      # scarce donor kept in stock
    assert held.weights["reserve_w"] == 0.2

    # the knob is selection-only: the booked CO2 of the kept pair is exactly what a plain IPE240
    # match would book (the reserve penalty lives in the score, never in co2_saved_kg)
    abund_only = match([supply[1]], [slot], cat)
    assert held.assignments[0].co2_saved_kg == pytest.approx(
        abund_only.assignments[0].co2_saved_kg, abs=0.01)

    # ...and the result still verifies: reserve_w travels on weights, and the audit re-applies the
    # penalty over the FULL cell matrix, so the (now-penalised) scarce donor is not an improving move
    assert verify_match(supply, [slot], cat, held) == []


def test_apply_reserve_exempts_single_family_slots_and_scales_with_scarcity():
    # Unit test of the mechanism itself, free of MILP economics. A scarce family A (one donor,
    # feasible for a shared slot AND a slot only it can serve) and an abundant family B (two donors,
    # the shared slot only). The penalty must: (i) leave the single-family slot untouched — spending
    # A there is the whole point of keeping it; (ii) fall hardest on the scarce, heavier family.
    from steelreuse.match.optimize import SupplyItem, _apply_reserve, _Cell

    f = load_factors()["steel"]
    supply = [SupplyItem(id="a", section="FAM_A", grade="S275", length_mm=5000),
              SupplyItem(id="b1", section="FAM_B", grade="S275", length_mm=5000),
              SupplyItem(id="b2", section="FAM_B", grade="S275", length_mm=5000)]

    def build_cells():
        # slot 0 = shared (A + B feasible); slot 1 = only A feasible
        return [
            _Cell(si=0, sj=0, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=100,
                  score=100.0, mass_used_kg=300.0),                      # A on shared
            _Cell(si=1, sj=0, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=100,
                  score=100.0, mass_used_kg=100.0),                      # B1 on shared
            _Cell(si=2, sj=0, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=100,
                  score=100.0, mass_used_kg=100.0),                      # B2 on shared
            _Cell(si=0, sj=1, utilization=.9, status="OK", offcut_mm=0, co2_saved_kg=50,
                  score=50.0, mass_used_kg=300.0),                       # A on its only slot
        ]

    # reserve_w = 0 changes nothing
    off = build_cells()
    _apply_reserve(off, supply, 0.0, f)
    assert [c.score for c in off] == [100.0, 100.0, 100.0, 50.0]

    # scarcity_A = |slots A| / |donors A| = 2 / 1 = 2.0 ; scarcity_B = 1 / 2 = 0.5 ; max = 2.0.
    on = build_cells()
    _apply_reserve(on, supply, 1.0, f)
    a_shared, b1_shared, _b2, a_only = on
    spk = f.saved_per_kg
    assert a_only.score == 50.0                                   # single-family slot exempt
    assert a_shared.score == pytest.approx(100.0 - (2.0 / 2.0) * 300.0 * spk)
    assert b1_shared.score == pytest.approx(100.0 - (0.5 / 2.0) * 100.0 * spk)
    # the scarce, heavier family is held back far harder than the abundant one
    assert (100.0 - a_shared.score) > (100.0 - b1_shared.score)


def test_reserve_default_keeps_results_identical(cat):
    # Explicit 0.0 must produce the exact same assignments and scores as not passing it at all.
    slot = _beam_slot(6000, 20.0)
    supply = [SupplyItem(id="a", section="IPE400", grade="S275", length_mm=7000),
              SupplyItem(id="b", section="IPE360", grade="S275", length_mm=6500)]
    base = match(supply, [slot], cat)
    explicit = match(supply, [slot], cat, reserve_w=0.0)
    assert [dataclasses.asdict(x) for x in base.assignments] == \
        [dataclasses.asdict(x) for x in explicit.assignments]
    assert explicit.weights["reserve_w"] == 0.0


def test_reserve_flows_through_pipeline_and_weights():
    from steelreuse.core.sections import load_default_catalog
    from steelreuse.match.optimize import verify_match

    donor = str(DATA / "samples" / "donor.json")
    demand = str(DATA / "samples" / "demand.json")
    base = run_pipeline(donor, demand)
    assert base.match.weights.get("reserve_w", 0.0) == 0.0

    res = run_pipeline(donor, demand, reserve_w=2.0)
    assert res.match.weights["reserve_w"] == 2.0
    # whatever the reserve does to selection, the shipped result remains internally consistent
    assert verify_match(res.supply, res.slots, load_default_catalog(), res.match) == []


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


def test_disposition_unused_reason_too_short_weak_contention(cat):
    # Tier 2: each unused donor says WHY it went unused, judged against the whole slot set.
    #   * "fits"  — feasible (and economic) for slot S -> contention, S in feasible_slot_ids;
    #   * "long"  — IPE200 cannot carry the slot at any length -> too-weak;
    #   * "stub"  — IPE300 is strong enough but the 1 m piece can reach nothing -> too-short.
    slot = _beam_slot(6000, 20.0, "S")
    supply = [
        SupplyItem(id="fits", section="IPE400", grade="S275", length_mm=7000),
        SupplyItem(id="long", section="IPE200", grade="S235", length_mm=8000),
        SupplyItem(id="stub", section="IPE300", grade="S275", length_mm=1000),
    ]
    real = match(supply, [slot], cat)
    review = dataclasses.replace(
        real, assignments=[], unmatched_slots=["S"], unused_supply=[s.id for s in supply])
    by_id = {r["supply_id"]: r for r in stock_disposition(supply, [slot], cat, review)}

    assert by_id["fits"]["reason"] == "contention"
    assert "S" in by_id["fits"]["feasible_slot_ids"]
    assert "S" in by_id["fits"]["reason_detail"]
    assert by_id["long"]["reason"] == "too-weak"
    assert by_id["long"]["feasible_slot_ids"] == []
    assert by_id["stub"]["reason"] == "too-short"
    assert "too short" in by_id["stub"]["reason_detail"]


# ---------------------------------------------------------------------------
# Tier 4 — per-donor what-if marginal value (re-solve with the donor removed)
# ---------------------------------------------------------------------------

def test_marginal_value_zero_when_a_substitute_exists(cat):
    # Two identical donors, one slot: the match uses one; removing it lets the twin take the slot at
    # the same saving -> marginal value ~ 0 (the result does not lean on that specific donor).
    from steelreuse.match.optimize import donor_marginal_value
    slot = _beam_slot(6000, 20.0, "S0")
    supply = [SupplyItem(id="d1", section="IPE360", grade="S275", length_mm=7000),
              SupplyItem(id="d2", section="IPE360", grade="S275", length_mm=7000)]
    res = match(supply, [slot], cat)
    assert res.n_reused == 1
    (r,) = donor_marginal_value(supply, [slot], cat, res)   # only the reused donor is analysed
    assert r["supply_id"] == res.assignments[0].supply_id
    assert r["marginal_co2_kg"] == pytest.approx(0.0, abs=0.01)
    assert r["slots_lost"] == [] and r["n_reused_without"] == 1 and r["reshuffled_slots"] == 0


def test_marginal_value_full_when_no_substitute(cat):
    # The only feasible donor for a slot is worth its whole booked saving: remove it and the slot
    # cannot be filled at all.
    from steelreuse.match.optimize import donor_marginal_value
    slot = _beam_slot(6000, 20.0, "S0")
    supply = [SupplyItem(id="only", section="IPE360", grade="S275", length_mm=7000)]
    res = match(supply, [slot], cat)
    (r,) = donor_marginal_value(supply, [slot], cat, res)
    assert r["supply_id"] == "only" and r["slots_lost"] == ["S0"]
    assert r["n_reused_without"] == 0 and r["n_reused_delta"] == -1
    assert r["co2_saved_without_kg"] == 0.0
    assert r["marginal_co2_kg"] == pytest.approx(res.total_co2_saved_kg, abs=0.05)


def test_marginal_value_only_via_pipeline_flag_and_renders():
    from steelreuse.llm.report import build_report_context, render_html

    donor, demand = str(DATA / "samples" / "donor.json"), str(DATA / "samples" / "demand.json")
    off = run_pipeline(donor, demand)
    assert off.marginal_value is None                       # off by default (one solve per donor)
    on = run_pipeline(donor, demand, donor_value=True)
    assert on.marginal_value and len(on.marginal_value) == on.match.n_reused
    assert all(r["marginal_co2_kg"] >= -0.01 for r in on.marginal_value)  # never improves on optimum
    html = render_html(build_report_context(on), "narrative")
    assert "Donor what-if value" in html
    assert "Donor what-if value" not in render_html(build_report_context(off), "narrative")


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
    # Tier 2: every unused donor falls in exactly one why-unused bucket, summing to the total.
    assert sum(totals["by_reason"].values()) == totals["n"]
    assert all(r["reason"] in ("too-short", "too-weak", "contention", "uneconomic")
               for r in res.disposition)
    html = render_html(ctx, "narrative")
    assert "Stock disposition" in html
    assert "Why each went unused" in html

    # without the advisory the section is absent
    ctx_off = build_report_context(run_pipeline(
        str(DATA / "samples" / "donor.json"), str(DATA / "samples" / "demand.json")))
    assert not ctx_off["disposition_present"]
    assert "Stock disposition" not in render_html(ctx_off, "narrative")
