"""PyNite ↔ SAP2000 cross-software parity — SKIPPED unless the SAP2000 OAPI is actually reachable.

This is the on-machine validation gate for the experimental SAP2000 backend (and the sign/axis
mapping in particular). In CI — and on any box without SAP2000 + the ``[sap2000]`` extra — every test
here skips, honouring the project rule that tests must never *require* SAP2000
(docs/DESIGN_PRINCIPLES.md). Run it on
the trial machine to confirm the two solvers agree on the validated canonical frame.
"""

import pytest

from steelreuse.benchmark.sap2000_bench import canonical_two_bay_frame, member_force_summary
from steelreuse.core._sap2000 import Sap2000Unavailable, sap2000_session
from steelreuse.core.frame import analyze_frame
from steelreuse.core.frame_sap2000 import analyze_frame_sap2000
from steelreuse.core.loads import AreaLoadModel
from steelreuse.core.sections import load_default_catalog

_TOL_PCT = 2.0          # PyNite vs SAP2000 agreement tolerance (%)
_ABS_FLOOR = 1_000.0    # ignore components below this (N or N·mm) — numerical noise near zero


def _require_sap2000():
    pytest.importorskip("Pynite")   # the PyNite reference itself needs the [fea] extra
    try:
        with sap2000_session():
            pass
    except Sap2000Unavailable as exc:
        pytest.skip(f"SAP2000 OAPI not available: {exc}")


def _agree(a: float, b: float) -> bool:
    if abs(a) < _ABS_FLOOR and abs(b) < _ABS_FLOOR:
        return True
    ref = max(abs(a), abs(b))
    return abs(a - b) / ref * 100.0 <= _TOL_PCT


def test_sap2000_matches_pynite_on_the_canonical_frame():
    _require_sap2000()
    members = canonical_two_bay_frame()
    loads = AreaLoadModel()
    catalog = load_default_catalog()

    pyn = analyze_frame(members, loads, catalog)
    sap = analyze_frame_sap2000(members, loads, catalog)
    assert pyn.ok and sap.ok

    py_summary = member_force_summary(pyn)
    sap_summary = member_force_summary(sap)
    assert set(py_summary) == set(sap_summary)

    mismatches = []
    for mid, pf in py_summary.items():
        sf = sap_summary[mid]
        for comp in ("N", "M", "V"):
            a, b = getattr(pf, comp), getattr(sf, comp)
            if not _agree(a, b):
                mismatches.append(f"{mid}.{comp}: pynite={a:.1f} sap2000={b:.1f}")
    assert not mismatches, "PyNite/SAP2000 force mismatch:\n" + "\n".join(mismatches)
