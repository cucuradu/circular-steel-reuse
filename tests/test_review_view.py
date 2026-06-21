# tests/test_review_view.py
"""Tests for the review renderers (pure dict-in, string-out -- like the results view)."""

import csv
import io

from steelreuse.review_view import (
    pda_report_csv,
    problem_report_csv,
    render_pda_report,
    render_problem_report,
)

REVIEW = {
    "schema_version": 1,
    "members": [
        {"id": "D1", "role": "beam", "raw_section": "IPE300", "section": "IPE300",
         "mapping_method": "exact", "condition": "B", "verification": "documented",
         "knockdown": 0.902, "defects": "light corrosion", "recoverable_length_mm": 5800.0,
         "audited": True, "admitted": True, "has_coords": True,
         "issues": [], "worst_severity": None, "color": None},
        {"id": "D2", "role": "beam", "raw_section": "BAR <JOIST>", "section": None,
         "mapping_method": "unknown", "condition": "", "verification": "",
         "knockdown": 1.0, "defects": "", "recoverable_length_mm": None,
         "audited": False, "admitted": True, "has_coords": False,
         "issues": [["UNKNOWN_SECTION", "error"], ["NOT_AUDITED", "info"]],
         "worst_severity": "error", "color": [214, 39, 40]},
    ],
    "coverage": {"total": 2, "roles": {"beam": 2}, "mapped": 1, "fuzzy": 0, "unknown": 1,
                 "with_coords": 1, "columns": 0, "columns_with_coords": 0,
                 "audited": 1, "admitted": 1, "quarantined": 0, "avg_knockdown": 0.902,
                 "verification_counts": {"documented": 1}, "condition_counts": {"B": 1},
                 "issue_counts": {"UNKNOWN_SECTION": 1, "NOT_AUDITED": 1}},
}


def test_problem_report_lists_problem_members_and_escapes():
    html = render_problem_report(REVIEW)
    assert "D2" in html
    assert "UNKNOWN_SECTION" in html
    assert "BAR &lt;JOIST&gt;" in html  # escaped
    assert "<JOIST>" not in html
    assert "D1" not in _problem_rows(html)   # clean member is not a problem row


def _problem_rows(html):
    # crude: the rows section only (everything after the first <tbody>)
    return html.split("<tbody", 1)[-1]


def test_problem_report_has_severity_kpis_and_filter():
    html = render_problem_report(REVIEW)
    assert "srxFilter" in html               # reused filter JS
    assert "srx-filter-severity" in html
    assert "error" in html and "info" in html


def test_pda_report_shows_coverage_and_member_audit():
    html = render_pda_report(REVIEW)
    assert "0.902" in html                    # avg knockdown
    assert "documented" in html               # verification
    assert "D1" in html and "light corrosion" in html


def test_pda_report_needs_attention_lists_unaudited_member():
    html = render_pda_report(REVIEW)
    needs = html.split("Needs attention", 1)
    assert len(needs) == 2                     # the section exists
    assert "D2" in needs[1] and "not audited" in needs[1]   # the unaudited member is flagged


def test_problem_report_csv_has_one_row_per_problem_member():
    rows = list(csv.DictReader(io.StringIO(problem_report_csv(REVIEW))))
    ids = [r["id"] for r in rows]
    assert ids == ["D2"]                       # only members with issues
    assert "UNKNOWN_SECTION" in rows[0]["issues"]


def test_pda_csv_roundtrips_into_audit_columns():
    rows = list(csv.DictReader(io.StringIO(pda_report_csv(REVIEW))))
    assert rows[0]["id"] == "D1"
    # exact --pda column order so it feeds straight back into load_audit_csv
    assert list(rows[0].keys()) == ["id", "condition_grade", "verification_status",
                                    "knockdown", "recoverable_length_mm", "defects"]
    assert rows[0]["condition_grade"] == "B"
    # D2 is unaudited with knockdown 1.0 -> the knockdown cell is blanked, not "1.0", so a
    # re-imported audit CSV doesn't pin a spurious default knockdown onto it.
    assert rows[1]["id"] == "D2"
    assert rows[1]["knockdown"] == ""
