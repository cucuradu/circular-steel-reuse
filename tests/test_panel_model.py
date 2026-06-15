"""Tests for the SteelReuse panel's headless view-model (lib/steelreuse_panel_model.py).

Pure parsing/filtering of the results.json v2 contract -- no Revit, no WPF -- so it runs under
CPython here exactly as it will under IronPython in Revit. Loaded by path like test_runner.py,
since the module lives under pyrevit_extension/.
"""

import importlib.util
import os

_LIB = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                    "SteelReuse.extension", "lib", "steelreuse_panel_model.py")
_spec = importlib.util.spec_from_file_location("steelreuse_panel_model", _LIB)
model = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(model)

_SAMPLE = {
    "schema_version": 2,
    "kpis": {"slots": 3, "reused": 2, "co2_saved_kg": 1000.0, "objective": "co2",
             "proven_optimal": True, "mass_reused_kg": 500.0, "distinct_sections": 1,
             "max_distinct_sections": None, "reuse_rate_pct": 67, "match_optimality": "x",
             "supply_count": 5},
    "assignments": [
        {"demand_id": "N1", "slot_id": "N1#0", "demand_section": "W16X26", "donor_id": "D1",
         "donor_section": "W18X55", "utilization": 0.40, "governing_combo": "ULS gravity",
         "check_status": "OK", "chi_lt": 1.0, "chi_lt_if_free": 0.7, "offcut_mm": 100.0,
         "co2_saved_kg": 600.0, "connection": "ok", "connection_review": False,
         "verification": "—", "condition": "—", "knockdown": 1.0},
        {"demand_id": "N2", "slot_id": "N2#0", "demand_section": "W16X26", "donor_id": "D2",
         "donor_section": "W16X26", "utilization": 0.95, "governing_combo": "ULS gravity",
         "check_status": "REVIEW", "chi_lt": None, "chi_lt_if_free": None, "offcut_mm": 0.0,
         "co2_saved_kg": 400.0, "connection": "review", "connection_review": True,
         "verification": "—", "condition": "—", "knockdown": 1.0}],
    "unfilled": [{"demand_id": "N3", "slot_id": "N3#0", "demand_section": "W16X26"}],
    "quarantined_donors": [], "diagnosis": {"binding_constraint": "length", "lever": "splice"},
    "warnings": {"ltb_restraint_reliant": 1, "imperfection_governed": 0, "cut_donors": 1,
                 "reusable_remainder_m": 2.0, "unknown": 0, "unknown_breakdown": [],
                 "connection_review": 1}, "paths": {}}


def test_parse_rejects_wrong_schema_version():
    bad = dict(_SAMPLE, schema_version=1)
    assert model.parse(bad).schema_ok is False
    assert model.parse(_SAMPLE).schema_ok is True


def test_rows_carry_display_status_chip():
    rows = model.parse(_SAMPLE).rows
    assert rows[0].status == "filled" and rows[0].restraint_warn is True   # chi_lt 1.0, free 0.7<0.85
    assert rows[1].status == "review" and rows[1].restraint_warn is False


def test_filter_by_status():
    rows = model.parse(_SAMPLE).rows
    assert [r.donor_id for r in model.filter_rows(rows, status="review")] == ["D2"]
    assert len(model.filter_rows(rows, status="all")) == 2


def test_filter_by_section_substring_and_min_util():
    rows = model.parse(_SAMPLE).rows
    assert [r.donor_id for r in model.filter_rows(rows, section="W18")] == ["D1"]
    assert [r.donor_id for r in model.filter_rows(rows, min_util=0.5)] == ["D2"]


def test_kpis_and_blocks_exposed():
    v = model.parse(_SAMPLE)
    assert v.kpis["reused"] == 2
    assert v.diagnosis["binding_constraint"] == "length"
    assert v.has_pareto is False and v.has_disposition is False
