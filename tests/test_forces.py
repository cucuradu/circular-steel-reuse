"""Phase 2 tests: force backends + demand generation.

Key check: the PyNiteFEA solve of a simply-supported span must match the closed-form M=wL^2/8.
"""

import pytest

from steelreuse.core.forces import (
    AnalyticBackend,
    Load,
    PyNiteBackend,
    member_demands,
)
from steelreuse.schema import ExtractedMember


def test_analytic_simply_supported():
    M, V = AnalyticBackend().beam_span_forces(span_mm=6000.0, udl_Npmm=10.0)
    assert M == pytest.approx(10.0 * 6000**2 / 8)   # 45e6 N*mm
    assert V == pytest.approx(10.0 * 6000 / 2)      # 30e3 N


def test_pynite_matches_analytic():
    span, w = 6000.0, 10.0
    Ma, Va = AnalyticBackend().beam_span_forces(span, w)
    Mp, Vp = PyNiteBackend().beam_span_forces(span, w)
    assert Mp == pytest.approx(Ma, rel=1e-3)
    assert Vp == pytest.approx(Va, rel=1e-3)


def test_member_demands_beam_one_per_span():
    bm = ExtractedMember(id="N1", role="beam", length_mm=12000, spans_mm=[6000, 6000])
    demands = member_demands(bm, Load(udl_Npmm=10.0))
    assert len(demands) == 2
    assert demands[0].My_Ed == pytest.approx(45e6)
    assert demands[0].w_service == pytest.approx(10.0)  # defaults to udl


def test_member_demands_column_is_axial():
    col = ExtractedMember(id="C1", role="column", length_mm=3500, spans_mm=[3500])
    demands = member_demands(col, Load(axial_N=400e3))
    assert len(demands) == 1
    assert demands[0].N_Ed == pytest.approx(400e3)
    assert demands[0].L == pytest.approx(3500)
