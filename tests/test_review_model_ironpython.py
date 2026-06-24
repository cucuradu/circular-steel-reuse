"""The donor-Review window's headless view-model flattens review.json into grid rows (loaded by path,
like the other extension-side models). Mirrors how it runs under IronPython in Revit."""

import importlib.util
import os

_LIB = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                    "SteelReuse.extension", "lib", "steelreuse_review_model.py")
_spec = importlib.util.spec_from_file_location("steelreuse_review_model", _LIB)
model = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(model)


def _review():
    return {
        "schema_version": 1,
        "coverage": {"total": 3, "unknown": 1, "fuzzy": 1, "mapped": 1,
                     "audited": 2, "admitted": 1, "quarantined": 1, "avg_knockdown": 0.85},
        "members": [
            {"id": "101", "role": "beam", "raw_section": "Wxx", "section": None,
             "mapping_method": "unknown", "worst_severity": "error",
             "issues": [["UNKNOWN_SECTION", "error"], ["MISSING_GRADE", "warn"]],
             "condition": "C", "verification": "visual", "knockdown": 0.9,
             "admitted": True, "defects": "pitting"},
            {"id": "102", "role": "column", "raw_section": "UC305", "section": "UC305X305X97",
             "mapping_method": "exact", "worst_severity": "", "issues": [],
             "condition": "", "verification": "", "knockdown": 1.0, "admitted": False, "defects": ""},
            {"id": "103", "role": "beam", "raw_section": "UB457", "section": "UB457X191X67",
             "mapping_method": "fuzzy", "worst_severity": "info",
             "issues": [["FUZZY_MATCH", "info"]],
             "condition": "B", "verification": "coupon", "knockdown": 0.8,
             "admitted": True, "defects": ""},
        ],
    }


def test_problem_rows_only_members_with_issues():
    rows = model.problem_rows(_review())
    assert [r.id for r in rows] == ["101", "103"]   # 102 is clean -> excluded
    r = rows[0]
    assert r.severity == "error"
    assert r.issues == "UNKNOWN_SECTION, MISSING_GRADE"  # codes joined, severity dropped to its column
    assert r.mapped == "-"                               # unmapped section renders as a dash


def test_pda_rows_cover_every_member_and_format_knockdown():
    rows = model.pda_rows(_review())
    assert [r.id for r in rows] == ["101", "102", "103"]  # audit covers all, not just problems
    assert rows[0].admitted == "yes" and rows[1].admitted == "no"
    assert rows[0].knockdown == "0.900"                   # numeric formatted to 3 dp
    assert rows[1].condition == "-"                       # blank condition -> dash


def test_summaries_are_one_line_headlines():
    rev = _review()
    psum = model.problem_summary(rev)
    assert "2 / 3 members need attention" in psum and "1 unknown" in psum
    assert "0.850" in model.pda_summary(rev) and "2 / 3 audited" in model.pda_summary(rev)
