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

DATA = Path(__file__).resolve().parents[1] / "src" / "steelreuse" / "data"


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


def test_unsupported_span_joints_merge_without_column():
    # The extractor records a span split at every crossing member endpoint (the frame solver needs
    # those nodes), but a joist framing into a girder LOADS it, it does not support it. On the
    # analytic path, joints with no column underneath must merge back so the girder is checked over
    # its real span (M ~ L^2) and demands a full-length donor, not five short ones.
    def _model(interior_col_x=None):
        members = [
            ExtractedMember(id="G1", role="beam", raw_section="IPE300", length_mm=7620.0,
                            spans_mm=[1524.0] * 5,
                            start_xyz=[0.0, 0.0, 3000.0], end_xyz=[7620.0, 0.0, 3000.0]),
            ExtractedMember(id="C1", role="column", raw_section="HEB200", length_mm=3000.0,
                            start_xyz=[0.0, 0.0, 0.0], end_xyz=[0.0, 0.0, 3000.0]),
            ExtractedMember(id="C2", role="column", raw_section="HEB200", length_mm=3000.0,
                            start_xyz=[7620.0, 0.0, 0.0], end_xyz=[7620.0, 0.0, 3000.0]),
        ]
        if interior_col_x is not None:
            members.append(ExtractedMember(
                id="C3", role="column", raw_section="HEB200", length_mm=3000.0,
                start_xyz=[interior_col_x, 0.0, 0.0], end_xyz=[interior_col_x, 0.0, 3000.0]))
        return ExtractedModel(kind="demand", members=members)

    # Columns at the ends only -> all four interior joints are joist crossings -> one full-span slot.
    g = [s for s in build_slots(_model()) if s.member_id == "G1"]
    assert len(g) == 1
    assert g[0].required_length_mm == pytest.approx(7620.0)
    assert g[0].demand.My_Ed == pytest.approx(15.0 * 7620.0**2 / 8.0)

    # A column under the second joint (x = 3048) is a real support -> two spans, 3048 + 4572.
    g = [s for s in build_slots(_model(interior_col_x=3048.0)) if s.member_id == "G1"]
    assert [s.required_length_mm for s in g] == [pytest.approx(3048.0), pytest.approx(4572.0)]

    # Columns without coordinates -> supports unverifiable -> extracted spans kept (legacy models).
    legacy = _model()
    for m in legacy.members:
        if m.role == "column":
            m.start_xyz = m.end_xyz = None
    assert len([s for s in build_slots(legacy) if s.member_id == "G1"]) == 5


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


def test_envelope_reports_governing_combination(cat):
    # #2 load-combination envelope: a column slot carrying two combinations (pure-axial gravity and a
    # gravity+notional-moment sway case) must be checked against both, with the worse one reported as
    # governing. Both pass here, but the sway case has the higher utilisation.
    grav = MemberDemand(N_Ed=300e3, L=4000)
    sway = MemberDemand(N_Ed=300e3, My_Ed=40e6, L=4000)
    supply = [SupplyItem(id="s", section="HEB300", grade="S355", length_mm=4500)]
    enveloped = DemandSlot(
        id="C", member_id="c", role="column", required_length_mm=4000, demand=grav,
        demands=[("ULS gravity", grav), ("ULS gravity + sway imperfection", sway)],
    )
    a = match(supply, [enveloped], cat).assignments[0]
    assert a.governing_combination == "ULS gravity + sway imperfection"

    grav_only = DemandSlot(id="C", member_id="c", role="column", required_length_mm=4000, demand=grav)
    b = match(supply, [grav_only], cat).assignments[0]
    assert b.governing_combination == "ULS gravity"
    assert a.utilization > b.utilization  # the sway-imperfection case is the worse, governing one


def test_baseline_requires_passing_every_combination(cat):
    # The avoided-new baseline must be the lightest section that passes the *whole* envelope, not just
    # gravity: adding a sway-imperfection moment forces a heavier (moment-capable) baseline section.
    grav = MemberDemand(N_Ed=200e3, L=4000)
    sway = MemberDemand(N_Ed=200e3, My_Ed=120e6, L=4000)
    grav_only = DemandSlot(id="g", member_id="c", role="column", required_length_mm=4000,
                           demand=grav, grade="S355")
    enveloped = DemandSlot(
        id="e", member_id="c", role="column", required_length_mm=4000, demand=grav, grade="S355",
        demands=[("ULS gravity", grav), ("ULS gravity + sway imperfection", sway)],
    )
    base_grav = baseline_new_mass_kg(grav_only, cat, "S355")
    base_env = baseline_new_mass_kg(enveloped, cat, "S355")
    assert base_grav is not None and base_env is not None
    assert base_env > base_grav


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


def test_cutting_stock_one_donor_fills_multiple_slots(cat):
    # #4 cutting-stock: the default one-piece model REJECTS a 9 m donor in a 4 m slot — the 5 m off-cut
    # penalty makes it net-negative (exactly the long-stock bias cutting-stock removes). Cutting-stock
    # instead cuts the donor into two 4 m pieces, fills both slots, and reports its reusable remainder.
    s1 = _beam_slot(4000, 8.0, "A")
    s2 = _beam_slot(4000, 8.0, "B")
    supply = [SupplyItem(id="long", section="IPE360", grade="S275", length_mm=9000)]

    default = match(supply, [s1, s2], cat)
    assert default.n_reused == 0                          # long stock rejected by the off-cut penalty

    cut = match(supply, [s1, s2], cat, allow_cutting=True)
    assert cut.n_reused == 2                              # one donor cut into two pieces
    assert {a.supply_id for a in cut.assignments} == {"long"}
    # leftover = 9000 - 2*(4000 + 50 cut tolerance) = 900 mm
    assert cut.donor_leftover_mm["long"] == pytest.approx(900.0, abs=1.0)
    assert cut.total_co2_saved_kg > default.total_co2_saved_kg


def test_cutting_stock_respects_donor_length(cat):
    # Three 4 m slots but a 9 m donor only yields two pieces (3*(4000+50) = 12150 mm > 9000 mm).
    slots = [_beam_slot(4000, 8.0, f"S{i}") for i in range(3)]
    supply = [SupplyItem(id="long", section="IPE360", grade="S275", length_mm=9000)]
    res = match(supply, slots, cat, allow_cutting=True)
    assert res.n_reused == 2
    assert len(res.unmatched_slots) == 1


def test_cutting_stock_greedy_packs_by_length():
    # The greedy fallback must also respect per-donor length capacity in cutting mode.
    from steelreuse.match.optimize import _Cell, _solve_greedy
    cells = [
        _Cell(si=0, sj=j, utilization=0.3, status="OK", offcut_mm=0.0,
              co2_saved_kg=10.0 - j, score=10.0 - j, used_len_mm=4000.0)
        for j in range(3)
    ]
    chosen = _solve_greedy(cells, n_supply=1, n_slots=3, caps=[9000.0])
    assert len(chosen) == 2     # only two 4050-mm pieces fit a 9000-mm donor


def test_milp_finds_the_global_optimum_where_greedy_cannot():
    # The classic crossing case: donor 0 fits both slots, donor 1 only slot 0. Taking the single
    # best pair first (greedy: 0->0, score 10) strands slot 1; the global optimum crosses them
    # (1->0 + 0->1 = 17). The production path ships the MILP solution and only falls back to greedy
    # when optimality could NOT be proven — so "reused" really is the best attainable selection.
    from steelreuse.match.optimize import _Cell, _solve_greedy, _solve_milp

    cells = [
        _Cell(si=0, sj=0, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=10, score=10.0),
        _Cell(si=0, sj=1, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=9, score=9.0),
        _Cell(si=1, sj=0, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=8, score=8.0),
    ]
    greedy = _solve_greedy(cells, n_supply=2, n_slots=2)
    assert sum(c.score for c in greedy) == pytest.approx(10.0)   # local choice strands slot 1
    chosen, status = _solve_milp(cells, n_supply=2, n_slots=2, time_limit_s=30)
    assert status == "Optimal"
    assert {(c.si, c.sj) for c in chosen} == {(0, 1), (1, 0)}
    assert sum(c.score for c in chosen) == pytest.approx(17.0)   # proven global optimum


def test_objective_members_fills_more_slots_than_co2_when_they_diverge():
    # Donor 0 fits both slots; donor 1 fits slot 0 only, at a NEGATIVE net CO2. The co2 objective
    # takes the single best pair (10 beats 9 + nothing, the negative cell is never selected); the
    # members objective fills BOTH slots (count dominates, CO2 only breaks ties) and honestly
    # books the negative entry.
    from steelreuse.match.optimize import _apply_objective, _Cell, _solve_milp

    def cells():
        return [
            _Cell(si=0, sj=0, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=10, score=10.0),
            _Cell(si=0, sj=1, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=9, score=9.0),
            _Cell(si=1, sj=0, utilization=.5, status="OK", offcut_mm=0, co2_saved_kg=-2, score=-2.0),
        ]
    by_co2 = cells()
    _apply_objective(by_co2, "co2")
    chosen, status = _solve_milp(by_co2, n_supply=2, n_slots=2, time_limit_s=30)
    assert status == "Optimal"
    assert {(c.si, c.sj) for c in chosen} == {(0, 0)}            # 10 > 9 + (never take -2)

    by_members = cells()
    _apply_objective(by_members, "members")
    chosen, status = _solve_milp(by_members, n_supply=2, n_slots=2, time_limit_s=30)
    assert status == "Optimal"
    assert {(c.si, c.sj) for c in chosen} == {(0, 1), (1, 0)}    # two filled slots beat one


def test_objective_mass_prefers_the_heavier_donor(cat):
    # Both donors pass the same slot. Net CO2 prefers the lighter one (less recovery/refab carbon
    # for the same avoided-new baseline); the mass objective puts the most reclaimed steel back
    # to work and picks the heavier section.
    slot = _beam_slot(6000, 20.0)
    supply = [SupplyItem(id="light", section="IPE300", grade="S275", length_mm=7000),
              SupplyItem(id="heavy", section="IPE550", grade="S275", length_mm=7000)]
    by_co2 = match(supply, [slot], cat)
    assert by_co2.proven_optimal and by_co2.objective == "co2"
    assert by_co2.assignments[0].supply_id == "light"
    by_mass = match(supply, [slot], cat, objective="mass")
    assert by_mass.proven_optimal and by_mass.objective == "mass"
    assert by_mass.assignments[0].supply_id == "heavy"


def test_objective_members_admits_a_net_negative_reuse(cat):
    # A short lightly-loaded slot whose only donor is a long heavy IPE600: the net-CO2 score is
    # negative (off-cut + recovery carbon dwarf the small avoided-new baseline) so the default
    # objective leaves the slot empty — but "members" reuses it, because the goal says fill slots.
    slot = _beam_slot(5000, 5.0, "small")
    supply = [SupplyItem(id="huge", section="IPE600", grade="S355", length_mm=9000)]
    assert match(supply, [slot], cat).n_reused == 0
    res = match(supply, [slot], cat, objective="members")
    assert res.n_reused == 1
    assert res.assignments[0].supply_id == "huge"


def test_verify_match_judges_by_the_result_objective(cat):
    # The verifier must use the objective the result was solved for: dropping an assignment from a
    # members-objective result is an improving move even where the pair's net CO2 is negative.
    from steelreuse.match.optimize import verify_match

    slot = _beam_slot(5000, 5.0, "small")
    supply = [SupplyItem(id="huge", section="IPE600", grade="S355", length_mm=9000)]
    res = match(supply, [slot], cat, objective="members")
    assert res.n_reused == 1
    assert verify_match(supply, [slot], cat, res) == []
    emptied = dataclasses.replace(res, assignments=[])
    issues = verify_match(supply, [slot], cat, emptied)
    assert any("improving move missed" in i for i in issues)
    # ...whereas under the default co2 objective an empty result for this pair is correct.
    assert verify_match(supply, [slot], cat,
                        dataclasses.replace(emptied, weights={})) == []


def test_run_pipeline_passes_the_objective_through():
    res = run_pipeline(str(DATA / "samples" / "donor.json"), str(DATA / "samples" / "demand.json"),
                       objective="members")
    assert res.match.objective == "members"
    assert res.match.proven_optimal


def test_pareto_view_solves_every_objective_without_changing_the_result():
    res = run_pipeline(str(DATA / "samples" / "donor.json"), str(DATA / "samples" / "demand.json"),
                       pareto=True)
    assert res.pareto is not None
    assert [p["objective"] for p in res.pareto] == ["co2", "members", "mass"]
    co2, members, mass = res.pareto
    # the shipped result IS the selected row — pareto is reporting, not a change of assignment
    assert co2["selected"] and not members["selected"] and not mass["selected"]
    assert co2["n_reused"] == res.match.n_reused
    assert co2["co2_saved_kg"] == pytest.approx(res.match.total_co2_saved_kg, abs=0.1)
    # dominance in each row's own currency: nothing fills more slots than "members" and nothing
    # reuses more steel than "mass" (1 kg slack: the CO2 tie-break may trade sub-unit primary).
    # NOTE no such claim for booked CO2 — the co2 objective maximizes score (CO2 minus the soft
    # off-cut preference), so another row may legitimately book more headline CO2.
    assert members["n_reused"] == max(p["n_reused"] for p in res.pareto)
    assert mass["mass_reused_kg"] >= max(p["mass_reused_kg"] for p in res.pareto) - 1.0
    assert all(p["proven_optimal"] for p in res.pareto)

    # the report context carries the rows with human labels
    from steelreuse.llm.report import build_report_context
    ctx = build_report_context(res)
    assert len(ctx["pareto"]) == 3 and ctx["pareto"][0]["label"] == "net-CO2"


def test_verify_match_certifies_the_result_and_catches_corruption(cat):
    # The independent audit re-derives every feasible pair and must (a) pass a genuine MILP result
    # and (b) flag a tampered one — both a missed improving move and a violated use constraint.
    from steelreuse.match.optimize import verify_match

    supply = [SupplyItem(id="d1", section="IPE360", grade="S275", length_mm=7000),
              SupplyItem(id="d2", section="IPE300", grade="S275", length_mm=7000)]
    slots = [_beam_slot(6000, 20.0, "s1"), _beam_slot(6000, 15.0, "s2")]
    res = match(supply, slots, cat)
    assert res.proven_optimal
    assert len(res.assignments) == 2
    assert verify_match(supply, slots, cat, res) == []

    # dropping an assignment frees a (donor, slot) pair the audit must rediscover
    broken = dataclasses.replace(res, assignments=res.assignments[1:])
    assert any("improving" in i for i in verify_match(supply, slots, cat, broken))

    # doubling one donor onto both slots violates one-piece-per-donor
    a0, a1 = res.assignments
    doubled = dataclasses.replace(
        res, assignments=[a0, dataclasses.replace(a1, supply_id=a0.supply_id)])
    assert any("used 2 times" in i for i in verify_match(supply, slots, cat, doubled))


def test_pipeline_result_carries_supply_and_verifies_end_to_end():
    # run_pipeline now exposes the admitted supply so the CLI --verify-match audit (and anyone
    # downstream) can re-check the shipped assignment without re-running the pipeline. The audit
    # must use the SAME catalog the run used (the merged default one) — re-checking against the
    # EU-only catalog flags pairs whose donor section only exists in the merged catalog, which is
    # exactly the input-drift class of defect verify_match exists to catch.
    from steelreuse.core.sections import load_default_catalog
    from steelreuse.match.optimize import verify_match

    res = run_pipeline(str(DATA / "samples" / "donor.json"), str(DATA / "samples" / "demand.json"))
    assert res.supply and len(res.supply) == res.supply_count
    assert res.match.proven_optimal
    assert verify_match(res.supply, res.slots, load_default_catalog(), res.match) == []


def test_wind_uplift_governs_and_can_reject_a_slender_roof_beam(cat):
    # Load-reversal end to end: a light roof (g_k = 0.5 kPa) under strong net suction (8 kPa).
    # Net upward w = (1.5*8 - 0.5)*3 = 34.5 N/mm -> M = 155.25 kNm with the BOTTOM flange in
    # compression (no slab restrains it). An IPE300 donor passes the gravity case restrained
    # (M = 69.9 kNm << M_c,Rd = 173 kNm) but its unrestrained M_b,Rd(6 m) ~ 77.7 kNm fails the
    # reversal; a roomy IPE500 passes with the uplift case reported as governing.
    from steelreuse.core.loads import AreaLoadModel
    from steelreuse.pipeline import build_slots
    from steelreuse.schema import ExtractedMember, ExtractedModel

    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="r1", role="beam", section="IPE300", raw_section="IPE300",
                        material_grade="S275", length_mm=6000, spans_mm=[6000],
                        start_xyz=[0, 0, 6000], end_xyz=[6000, 0, 6000]),
    ])
    slots = build_slots(demand, AreaLoadModel(dead_kpa=0.5, uplift_kpa=8.0))

    rejected = match([SupplyItem(id="d300", section="IPE300", grade="S275", length_mm=7000)],
                     slots, cat)
    assert rejected.assignments == []   # fails the reversal despite passing gravity

    ok = match([SupplyItem(id="d500", section="IPE500", grade="S275", length_mm=7000)], slots, cat)
    assert len(ok.assignments) == 1
    assert ok.assignments[0].governing_combination == "ULS wind uplift"

    # without uplift, under restrained gravity (deck restrains the top flange) the IPE300 is accepted —
    # isolating the uplift reversal as the cause of rejection above.
    slots_off = build_slots(demand, AreaLoadModel(dead_kpa=0.5, flange_restrained=True))
    accepted = match([SupplyItem(id="d300", section="IPE300", grade="S275", length_mm=7000)],
                     slots_off, cat)
    assert len(accepted.assignments) == 1


def test_construction_stage_governs_and_can_reject_a_slender_beam(cat):
    # #8 construction-stage case, end to end through build_slots + match: an IPE300-design beam slot
    # at 6 m / 3 m tributary. Under gravity (restrained, w = 27.675 N/mm) M_Ed = 124.5 kNm and an
    # IPE330 donor sits at 124.5/221.1 = 0.56. The erection-stage entry (w_c = 17.55 N/mm,
    # M = 78.975 kNm, UNRESTRAINED -> chi_LT * M_pl,Rd) is the worse case for the donor, so it must
    # be reported as governing. An IPE300 donor passes gravity restrained (0.72) but FAILS the
    # construction case (chi_LT(6 m) ~ 0.35 with top-flange/destabilising load -> M_b,Rd ~ 61 kNm <
    # 78.975), as does an IPE330 (M_b,Rd ~ 75 kNm); an IPE360 (M_b,Rd ~ 101 kNm) passes. So with the
    # stage enabled the slender donors may not be assigned to the slot.
    from steelreuse.core.loads import AreaLoadModel
    from steelreuse.pipeline import build_slots
    from steelreuse.schema import ExtractedMember, ExtractedModel

    demand = ExtractedModel(kind="demand", members=[
        ExtractedMember(id="b1", role="beam", section="IPE300", raw_section="IPE300",
                        material_grade="S275", length_mm=6000, spans_mm=[6000]),
    ])
    # service = slab-restrained; erection (construction_stage) = bare-steel unrestrained
    slots = build_slots(demand, AreaLoadModel(construction_stage=True, flange_restrained=True))

    ok = match([SupplyItem(id="d360", section="IPE360", grade="S275", length_mm=7000)], slots, cat)
    assert len(ok.assignments) == 1
    assert ok.assignments[0].governing_combination == "ULS construction stage"

    rejected = match([SupplyItem(id="d300", section="IPE300", grade="S275", length_mm=7000)],
                     slots, cat)
    assert rejected.assignments == []   # fails the bare-steel stage despite passing gravity

    # without the stage, the same IPE300 donor is accepted (gravity restrained only)
    slots_off = build_slots(demand, AreaLoadModel(flange_restrained=True))  # slab-restrained gravity
    accepted = match([SupplyItem(id="d300", section="IPE300", grade="S275", length_mm=7000)],
                     slots_off, cat)
    assert len(accepted.assignments) == 1
