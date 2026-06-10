"""Connection feasibility screen: geometric compatibility rules + matcher gating/annotation."""

import pytest

from steelreuse.core.connections import ConnectionPolicy, screen_pair
from steelreuse.core.ec3_checks import MemberDemand
from steelreuse.core.sections import load_default_catalog
from steelreuse.match.optimize import DemandSlot, SupplyItem, match

IN_MM = 25.4


@pytest.fixture(scope="module")
def catalog():
    return load_default_catalog()


# --- screen_pair rules -------------------------------------------------------

def test_no_design_section_gives_no_opinion(catalog):
    chk = screen_pair(catalog["IPE300"], None)
    assert chk.status == "unknown"


def test_same_section_is_ok(catalog):
    assert screen_pair(catalog["IPE300"], catalog["IPE300"]).status == "ok"


def test_family_mismatch_is_incompatible(catalog):
    chk = screen_pair(catalog["HSS6X6X1/2"], catalog["W12X26"])
    assert chk.status == "incompatible" and "typology" in chk.note
    assert screen_pair(catalog["W12X26"], catalog["HSS6X6X1/2"]).status == "incompatible"


def test_donor_too_deep_is_incompatible(catalog):
    # IPE500 standing in for an IPE300 slot: 200 mm deeper than the detailed zone allows.
    chk = screen_pair(catalog["IPE500"], catalog["IPE300"])
    assert chk.status == "incompatible" and "deeper" in chk.note


def test_slightly_deeper_within_tolerance_is_ok(catalog):
    # IPE330 in an IPE300 slot: +30 mm <= the 50 mm default allowance.
    assert screen_pair(catalog["IPE330"], catalog["IPE300"]).status == "ok"


def test_much_shallower_donor_is_review_not_gate(catalog):
    chk = screen_pair(catalog["IPE200"], catalog["IPE300"])
    assert chk.status == "review" and "shallower" in chk.note


def test_thin_web_flags_bolt_bearing(catalog):
    # Same depth band, much thinner web than designed: W12X26 (tw 0.23 in) for W12X96 (tw 0.55 in).
    chk = screen_pair(catalog["W12X26"], catalog["W12X96"])
    assert chk.status == "review"
    assert any("bolt bearing" in n for n in chk.notes)


def test_policy_is_adjustable(catalog):
    relaxed = ConnectionPolicy(max_depth_over_mm=250.0)
    assert screen_pair(catalog["IPE500"], catalog["IPE300"], relaxed).status != "incompatible"


# --- matcher integration -----------------------------------------------------

def _beam_slot(design_section, grade="S355"):
    demand = MemberDemand(My_Ed=60e6, Vz_Ed=60e3, L=5000.0, compression_flange_restrained=True)
    return DemandSlot(id="s1", member_id="m1", role="beam", required_length_mm=5000.0,
                      demand=demand, grade=grade, design_section=design_section)


def test_screen_off_annotates_but_does_not_gate(catalog):
    # An IPE500 donor passes the EN check for an IPE300 slot and, with the screen OFF, is matched —
    # but the assignment carries the incompatibility annotation.
    supply = [SupplyItem(id="d1", section="IPE500", grade="S355", length_mm=6000.0)]
    res = match(supply, [_beam_slot("IPE300")], catalog)
    assert len(res.assignments) == 1
    assert res.assignments[0].connection_status == "incompatible"


def test_screen_on_gates_incompatible_pairs(catalog):
    supply = [SupplyItem(id="d1", section="IPE500", grade="S355", length_mm=6000.0)]
    res = match(supply, [_beam_slot("IPE300")], catalog, connection_policy=ConnectionPolicy())
    assert len(res.assignments) == 0
    assert res.unmatched_slots == ["s1"]


def test_screen_on_keeps_compatible_pairs_and_flags_review(catalog):
    supply = [
        SupplyItem(id="deep", section="IPE500", grade="S355", length_mm=6000.0),
        SupplyItem(id="shallow", section="IPE330", grade="S355", length_mm=6000.0),
    ]
    res = match(supply, [_beam_slot("IPE360")], catalog, connection_policy=ConnectionPolicy())
    assert len(res.assignments) == 1
    a = res.assignments[0]
    assert a.supply_id == "shallow"          # the deep donor was screened out
    assert a.connection_status in ("ok", "review")


def test_no_design_section_never_gates(catalog):
    supply = [SupplyItem(id="d1", section="IPE500", grade="S355", length_mm=6000.0)]
    res = match(supply, [_beam_slot(None)], catalog, connection_policy=ConnectionPolicy())
    assert len(res.assignments) == 1
    assert res.assignments[0].connection_status == "unknown"
