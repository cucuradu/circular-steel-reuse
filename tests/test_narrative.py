"""Analytical narrative + the match diagnosis behind it.

The report narrative must DIAGNOSE the result (the binding constraint on reuse and the lever to
improve it) rather than recite counts — and, per docs/DESIGN_PRINCIPLES.md rule 1, every number is computed in
Python (`diagnose_match`) and only rendered by the narrative, so the LLM anti-hallucination guard
still passes when a live provider quotes them.
"""

from pathlib import Path

import pytest

from steelreuse.core.ec3_checks import MemberDemand
from steelreuse.core.forces import AnalyticBackend
from steelreuse.core.sections import load_catalog
from steelreuse.llm.report import (
    _allowed_numbers,
    build_report_context,
    deterministic_narrative,
    find_invented_numbers,
    generate_narrative,
)
from steelreuse.match.optimize import DemandSlot, SupplyItem, diagnose_match, match
from steelreuse.pipeline import run_pipeline

DATA = Path(__file__).resolve().parents[1] / "src" / "steelreuse" / "data" / "samples"


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def _beam_slot(span_mm, udl, slot_id="S0"):
    M, V = AnalyticBackend().beam_span_forces(span_mm, udl)
    d = MemberDemand(My_Ed=M, Vz_Ed=V, L=span_mm, compression_flange_restrained=True)
    return DemandSlot(id=slot_id, member_id="m", role="beam", required_length_mm=span_mm, demand=d)


# ---------------------------------------------------------------------------
# diagnose_match — the binding-constraint classification
# ---------------------------------------------------------------------------

def test_diagnose_length_limited(cat):
    # An adequate section (IPE360 passes the 6 m / 20 kN/m slot) but every donor is too short:
    # no feasible cell, yet the EN re-check passes -> the slot is length-limited, cutting is the lever.
    slot = _beam_slot(6000, 20.0)
    supply = [SupplyItem(id="short", section="IPE360", grade="S275", length_mm=3000)]
    res = match(supply, [slot], cat)
    assert res.n_reused == 0
    d = diagnose_match(supply, [slot], cat, res)
    assert d["binding_constraint"] == "length"
    assert d["length_limited"] == 1 and d["capacity_limited"] == 0
    assert "cutting" in d["lever"]


def test_diagnose_capacity_limited(cat):
    # Long enough but far too weak: IPE200 fails the 8 m / 40 kN/m slot at any length -> capacity.
    slot = _beam_slot(8000, 40.0)
    supply = [SupplyItem(id="weak", section="IPE200", grade="S235", length_mm=10000)]
    res = match(supply, [slot], cat)
    assert res.n_reused == 0
    d = diagnose_match(supply, [slot], cat, res)
    assert d["binding_constraint"] == "capacity"
    assert d["capacity_limited"] == 1 and d["length_limited"] == 0


def test_diagnose_contention(cat):
    # Two equal slots, one adequate donor: one fills, the other goes unfilled though a usable donor
    # existed for it (it was spent elsewhere) -> contention, not a length/capacity wall.
    slots = [_beam_slot(6000, 20.0, "S0"), _beam_slot(6000, 20.0, "S1")]
    supply = [SupplyItem(id="one", section="IPE360", grade="S275", length_mm=7000)]
    res = match(supply, slots, cat)
    assert res.n_reused == 1
    d = diagnose_match(supply, slots, cat, res)
    assert d["binding_constraint"] == "contention"
    assert d["contention"] == 1


def test_diagnose_all_filled_has_no_binding_constraint(cat):
    slot = _beam_slot(6000, 20.0)
    supply = [SupplyItem(id="ok", section="IPE360", grade="S275", length_mm=7000)]
    res = match(supply, [slot], cat)
    assert res.n_reused == 1
    d = diagnose_match(supply, [slot], cat, res)
    assert d["binding_constraint"] == "none" and d["n_unmatched"] == 0


def test_diagnose_flags_overspec_upgrade_match(cat):
    # A lightly loaded 4 m slot filled by a far heavier donor than the lightest section that would
    # pass it -> flagged over-spec, with the donor and a lighter alternative named for the narrative.
    slot = _beam_slot(4000, 8.0)
    supply = [SupplyItem(id="heavy", section="IPE500", grade="S355", length_mm=4200)]
    res = match(supply, [slot], cat)
    assert res.n_reused == 1 and res.assignments[0].utilization < 0.3   # grossly over-spec
    d = diagnose_match(supply, [slot], cat, res)
    assert d["n_overspec"] == 1
    assert d["overspec_example"]["donor"] == "IPE500"
    assert d["overspec_example"]["lighter"] != "IPE500"


def test_diagnose_right_sized_match_is_not_overspec(cat):
    # A donor that IS the lightest adequate section for the slot must never be flagged over-spec.
    from steelreuse.match.optimize import lightest_adequate_section
    slot = _beam_slot(6000, 20.0)
    lightest = lightest_adequate_section(slot, cat, "S355")
    supply = [SupplyItem(id="fit", section=lightest.name, grade="S355", length_mm=7000)]
    res = match(supply, [slot], cat)
    assert res.n_reused == 1
    assert diagnose_match(supply, [slot], cat, res)["n_overspec"] == 0


def test_narrative_reports_overspec_when_common():
    ctx = {
        "slot_count": 10, "n_reused": 8, "reuse_rate_pct": 80, "match_co2_saved_kg": 100.0,
        "n_unmatched": 0, "unknown": 0, "unknown_kinds": 0, "unknown_breakdown": [],
        "diagnosis": {"binding_constraint": "none", "lever": "", "n_unmatched": 0, "n_overspec": 5,
                      "overspec_example": {"donor": "W18X55", "lighter": "W12X26"}},
    }
    text = deterministic_narrative(ctx)
    assert "over-spec" in text and "W18X55" in text and "W12X26" in text and "--w-overspec" in text
    # below the reporting floor -> stays quiet (only noticeable upgrades are surfaced)
    ctx["diagnosis"]["n_overspec"] = 1
    assert "over-spec" not in deterministic_narrative(ctx)


# ---------------------------------------------------------------------------
# the narrative leads with the diagnosis, and the numbers stay guard-safe
# ---------------------------------------------------------------------------

def test_narrative_leads_with_diagnosis_and_lever():
    res = run_pipeline(str(DATA / "donor.json"), str(DATA / "demand.json"))
    ctx = build_report_context(res)
    assert "diagnosis" in ctx and ctx.get("reuse_rate_pct") is not None
    text = deterministic_narrative(ctx)
    assert "matched to reclaimed steel" in text
    if ctx["n_unmatched"]:
        assert "binding constraint is" in text          # diagnoses, not just recites
    out, source = generate_narrative(ctx)               # no provider -> the deterministic analysis
    assert source == "deterministic" and out == text


def test_diagnosis_numbers_are_guard_allowed_and_narrative_is_clean():
    res = run_pipeline(str(DATA / "donor.json"), str(DATA / "demand.json"))
    ctx = build_report_context(res)
    allowed = _allowed_numbers(ctx)
    d = ctx["diagnosis"]
    for key in ("length_limited", "capacity_limited", "contention", "uneconomic", "n_unmatched"):
        assert round(float(d[key]), 2) in allowed       # an LLM may quote these without rejection
    # the deterministic narrative must never trip the invented-number guard
    assert find_invented_numbers(deterministic_narrative(ctx), allowed) == []
