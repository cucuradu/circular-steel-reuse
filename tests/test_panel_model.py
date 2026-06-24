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


def _data(reused, co2, mass, distinct, assignments, unfilled):
    return {"schema_version": 2,
            "kpis": {"reused": reused, "slots": 5, "co2_saved_kg": co2, "mass_reused_kg": mass,
                     "distinct_sections": distinct, "objective": "co2", "proven_optimal": True,
                     "supply_count": 9, "reuse_rate_pct": 0, "match_optimality": "x",
                     "max_distinct_sections": None},
            "assignments": [{"slot_id": s, "donor_id": d, "demand_id": s.split("#")[0],
                             "demand_section": "W16X26", "donor_section": "W18X55",
                             "utilization": 0.5, "governing_combo": "ULS gravity",
                             "check_status": "OK", "chi_lt": 1.0, "chi_lt_if_free": None,
                             "offcut_mm": 0.0, "co2_saved_kg": 100.0, "connection": "ok",
                             "connection_review": False, "verification": "-", "condition": "-",
                             "knockdown": 1.0} for s, d in assignments],
            "unfilled": [{"slot_id": s, "demand_id": s.split("#")[0], "demand_section": "W16X26"}
                         for s in unfilled],
            "quarantined_donors": [], "diagnosis": {}, "warnings": {}, "paths": {}}


def test_diff_kpi_deltas():
    base = _data(90, 1000.0, 800.0, 8, [("N1#0", "D1")], ["N2#0"])
    cur = _data(71, 700.0, 600.0, 6, [("N1#0", "D1")], ["N2#0", "N3#0"])
    out = model.diff(base, cur)
    by = {r["label"]: r for r in out["kpis"]}
    assert by["Members reused"]["delta"] == -19
    assert by["CO2e saved (kg)"]["delta"] == -300.0
    assert by["Mass reused (kg)"]["delta"] == -200.0
    assert by["Distinct sections"]["delta"] == -2
    assert by["Unfilled slots"]["baseline"] == 1 and by["Unfilled slots"]["current"] == 2
    assert by["Unfilled slots"]["delta"] == 1


def test_diff_slot_changes_lost_gained_donor_and_unchanged():
    base = _data(2, 1.0, 1.0, 1, [("S_same#0", "D1"), ("S_lost#0", "D2"), ("S_donor#0", "D3")],
                 ["S_gain#0"])
    cur = _data(2, 1.0, 1.0, 1, [("S_same#0", "D1"), ("S_donor#0", "D9"), ("S_gain#0", "D8")],
                ["S_lost#0"])
    changes = {c["slot_id"]: c["change"] for c in model.diff(base, cur)["slots"]}
    assert changes["S_lost#0"] == "lost"
    assert changes["S_gain#0"] == "gained"
    assert changes["S_donor#0"] == "donor"
    assert "S_same#0" not in changes


def test_diff_slot_detail_strings():
    base = _data(1, 1.0, 1.0, 1, [("S_donor#0", "D3")], [])
    cur = _data(1, 1.0, 1.0, 1, [("S_donor#0", "D9")], [])
    detail = model.diff(base, cur)["slots"][0]["detail"]
    assert "D3" in detail and "D9" in detail


def test_diff_slot_changes_carry_zoom_ids():
    # Each change carries the demand element id + both donor ids so Compare can zoom to the member.
    base = _data(1, 1.0, 1.0, 1, [("S_donor#0", "D3"), ("S_lost#0", "D2")], ["S_gain#0"])
    cur = _data(1, 1.0, 1.0, 1, [("S_donor#0", "D9"), ("S_gain#0", "D8")], ["S_lost#0"])
    by = {c["slot_id"]: c for c in model.diff(base, cur)["slots"]}
    assert by["S_donor#0"]["demand_id"] == "S_donor"          # demand id stripped of the #span
    assert by["S_donor#0"]["donor_baseline"] == "D3" and by["S_donor#0"]["donor_current"] == "D9"
    assert by["S_lost#0"]["donor_baseline"] == "D2" and by["S_lost#0"]["donor_current"] is None
    assert by["S_gain#0"]["donor_baseline"] is None and by["S_gain#0"]["donor_current"] == "D8"


def test_kpi_table_columns_and_aligned_values():
    a = _data(90, 1000.0, 800.0, 8, [("N1#0", "D1")], ["N2#0"])
    b = _data(71, 700.0, 600.0, 6, [("N1#0", "D1")], ["N2#0", "N3#0"])
    t = model.kpi_table([("baseline", a), ("no-cut", b)])
    assert t["columns"] == ["baseline", "no-cut"]
    rows = {r["label"]: r["values"] for r in t["rows"]}
    assert rows["Members reused"] == [90, 71]
    assert rows["CO2e saved (kg)"] == [1000.0, 700.0]
    assert rows["Unfilled slots"] == [1, 2]
