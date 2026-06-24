"""Tests for the experimental steel-handling labour sketch (steelreuse.core.labour).

This module is parked, not wired into the value-case verdict (see core/labour.py docstring), but the
three-tier decomposition — per-member handling + shared per-joint disassembly — is still exercised.
"""

from steelreuse.core.labour import joint_hours, labour_estimate
from steelreuse.schema import ExtractedMember, ExtractedModel


def _m(mid, x0, x1, conn=None, axis="x"):
    a = [x0, 0.0, 0.0] if axis == "x" else [0.0, 0.0, x0]
    b = [x1, 0.0, 0.0] if axis == "x" else [0.0, 0.0, x1]
    return ExtractedMember(id=mid, raw_section="IPE300", section="IPE300",
                           start_xyz=a, end_xyz=b, connection_type=conn, length_mm=abs(x1 - x0))


def _donor(*members):
    return ExtractedModel(kind="donor", members=list(members))


def test_cost_is_hours_times_rate():
    est = labour_estimate(100.0, 6000.0, member_joint_hours=0.4, labour_rate_per_hour=60.0)
    assert abs(est.cost_gbp - est.hours * 60.0) < 0.01


def test_splits_into_handling_and_joints():
    est = labour_estimate(200.0, 6000.0, member_joint_hours=0.5)
    assert est.joint_hours == 0.5
    assert est.handling_hours > 0.0
    assert abs(est.hours - (est.handling_hours + est.joint_hours)) < 0.01


def test_handling_scales_with_mass():
    light = labour_estimate(100.0, 6000.0, member_joint_hours=0.0).hours
    heavy = labour_estimate(800.0, 6000.0, member_joint_hours=0.0).hours
    assert heavy > light


def test_joint_shared_not_double_counted():
    # Two beams meeting at one node: the joint is charged once across the topology, not per member.
    jh = joint_hours(_donor(_m("a", 0.0, 6000.0), _m("b", 6000.0, 12000.0)))
    # three nodes (0, 6000 shared, 12000), all bolted/unknown -> 0.3 each -> total 0.9
    assert abs(sum(jh.values()) - 0.9) < 1e-6
    assert jh["a"] < 0.6 and jh["b"] < 0.6  # shared node split, not 0.3 to each


def test_welded_joint_costs_more_than_bolted():
    bolted = joint_hours(_donor(_m("a", 0.0, 6000.0, conn="bolted"),
                                _m("b", 6000.0, 12000.0, conn="bolted")))
    welded = joint_hours(_donor(_m("a", 0.0, 6000.0, conn="welded"),
                                _m("b", 6000.0, 12000.0, conn="welded")))
    assert sum(welded.values()) > sum(bolted.values())


def test_column_share_shrinks_as_more_beams_frame_in():
    col = ExtractedMember(id="col", raw_section="HEB300", section="HEB300",
                          start_xyz=[0, 0, 0], end_xyz=[0, 0, 3000], length_mm=3000.0)

    def beam(mid, ex, ey):
        return ExtractedMember(id=mid, raw_section="IPE300", section="IPE300",
                               start_xyz=[0, 0, 3000], end_xyz=[ex, ey, 3000], length_mm=6000.0)

    one = joint_hours(_donor(col, beam("b1", 6000, 0)))
    many = joint_hours(_donor(col, beam("b1", 6000, 0), beam("b2", -6000, 0), beam("b3", 0, 6000)))
    assert many["col"] < one["col"]
