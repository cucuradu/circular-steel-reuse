# tests/test_audit_grid.py
"""Pure model behind the in-Revit audit grid (loaded by path; IronPython-safe)."""

import importlib.util
import os

_LIB = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                    "SteelReuse.extension", "lib", "steelreuse_audit_grid.py")
_spec = importlib.util.spec_from_file_location("steelreuse_audit_grid", _LIB)
grid = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grid)

_REVIEW = {"members": [
    {"id": "1", "mark": "B-1", "section": "IPE300", "role": "beam",
     "condition": "", "verification": "", "knockdown": 1.0, "defects": ""},
    {"id": "2", "mark": "B-2", "section": "IPE300", "role": "beam",
     "condition": "A", "verification": "documented", "knockdown": 0.95, "defects": ""},
]}


def test_rows_built_from_review():
    rows = grid.build_rows(_REVIEW)
    assert [r["id"] for r in rows] == ["1", "2"]
    assert rows[1]["condition_grade"] == "A"


def test_edit_marks_dirty_and_builds_payload():
    rows = grid.build_rows(_REVIEW)
    grid.set_value(rows[0], "condition_grade", "c")     # normalised on set
    assert rows[0]["condition_grade"] == "C"
    assert rows[0]["_dirty"] is True
    payload = grid.write_payload(rows)
    assert payload == {"1": {"condition_grade": "C"}}   # only dirty fields of dirty rows


def test_bulk_set_applies_to_selection():
    rows = grid.build_rows(_REVIEW)
    grid.bulk_set(rows, "verification_status", "mill certificate")
    assert all(r["verification_status"] == "mill_cert" for r in rows)
    assert grid.write_payload(rows) == {
        "1": {"verification_status": "mill_cert"},
        "2": {"verification_status": "mill_cert"},
    }
