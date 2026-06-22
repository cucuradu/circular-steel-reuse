"""Tests for the deconstruction recovery model (connection type -> recovery treatment + degree)."""

from steelreuse.core.deconstruction import (DeconstructionPolicy, deconstruction_treatment,
                                            effective_recoverable_length, member_degrees)
from steelreuse.schema import ExtractedMember, ExtractedModel


def _m(**kw):
    kw.setdefault("length_mm", 6000.0)
    return ExtractedMember(id="1", raw_section="IPE300", **kw)


def test_bolted_is_clean_recovery():
    t = deconstruction_treatment(_m(connection_type="bolted"))
    assert t.cut_total_mm == 0.0
    assert t.process_multiplier == 1.0


def test_welded_requires_cutting_both_ends():
    p = DeconstructionPolicy(cut_allowance_mm=60.0, welded_process_multiplier=1.4)
    t = deconstruction_treatment(_m(connection_type="welded"), p)
    assert t.cut_total_mm == 120.0          # both ends
    assert t.process_multiplier == 1.4


def test_unknown_or_absent_no_penalty():
    assert deconstruction_treatment(_m()).cut_total_mm == 0.0
    assert deconstruction_treatment(_m(connection_type="unknown")).process_multiplier == 1.0


def test_deconstructability_override_wins():
    # surveyed "easy" overrides a welded type -> treated as clean
    t = deconstruction_treatment(_m(connection_type="welded", deconstructability="easy"))
    assert t.cut_total_mm == 0.0


def test_effective_recoverable_length_composes_with_pda_and_floors():
    p = DeconstructionPolicy(cut_allowance_mm=60.0, min_stock_mm=1000.0)
    # PDA recoverable 5800, welded -> minus 120 -> 5680
    m = _m(connection_type="welded", recoverable_length_mm=5800.0)
    assert effective_recoverable_length(m, p) == 5680.0
    # floor: a tiny member can't go below min_stock
    short = _m(connection_type="welded", length_mm=1000.0, recoverable_length_mm=1000.0)
    assert effective_recoverable_length(short, p) == 1000.0


def test_member_degrees_on_a_small_frame():
    # two beams sharing one end node at (6000,0,0): each connects to the other -> degree 1 each.
    members = [
        ExtractedMember(id="b1", role="beam", raw_section="IPE300",
                        start_xyz=[0, 0, 0], end_xyz=[6000, 0, 0]),
        ExtractedMember(id="b2", role="beam", raw_section="IPE300",
                        start_xyz=[6000, 0, 0], end_xyz=[12000, 0, 0]),
    ]
    deg = member_degrees(ExtractedModel(kind="donor", members=members))
    assert deg["b1"] == 1
    assert deg["b2"] == 1


def test_member_without_coords_has_no_degree():
    m = ExtractedMember(id="x", role="beam", raw_section="IPE300")  # no coords
    deg = member_degrees(ExtractedModel(kind="donor", members=[m]))
    assert "x" not in deg
