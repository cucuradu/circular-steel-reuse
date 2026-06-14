"""Tests for the HTML results view (lib/steelreuse_results_view.py).

The renderer is a pure function: results.json dict -> an HTML string with a KPI header, a filterable
assignments table, and the unfilled / quarantined lists. It is shown in pyRevit's output window inside
Revit, but being pure + stdlib-only it is fully testable here (and IronPython-safe for Revit).
"""

import importlib.util
import os

_LIB = os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                    "SteelReuse.extension", "lib", "steelreuse_results_view.py")
_spec = importlib.util.spec_from_file_location("steelreuse_results_view", _LIB)
view = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(view)


_SAMPLE = {
    "schema_version": 1,
    "kpis": {"slots": 3, "reused": 2, "co2_saved_kg": 1200.0, "objective": "co2",
             "proven_optimal": True, "supply_count": 5},
    "assignments": [
        {"demand_id": "N1", "slot_id": "N1#0", "demand_section": "W16X26",
         "donor_id": "D1", "donor_section": "W18X55", "utilization": 0.71,
         "governing_combo": "ULS gravity", "check_status": "OK", "chi_lt": 1.0,
         "chi_lt_if_free": 0.6, "offcut_mm": 1000.0, "co2_saved_kg": 700.0,
         "connection_review": False},
        {"demand_id": "N2", "slot_id": "N2#0", "demand_section": "W16X26",
         "donor_id": "D2", "donor_section": "W21X44", "utilization": 0.55,
         "governing_combo": "ULS gravity", "check_status": "REVIEW", "chi_lt": None,
         "chi_lt_if_free": None, "offcut_mm": 0.0, "co2_saved_kg": 500.0,
         "connection_review": True},
    ],
    "unfilled": [{"demand_id": "N3", "slot_id": "N3#0", "demand_section": "W16X26"}],
    "quarantined_donors": [{"donor_id": "D3", "donor_section": "W18X55", "reason": "condition D"}],
}


def test_render_returns_html_with_kpis():
    html = view.render_results_html(_SAMPLE)
    assert isinstance(html, str)
    assert "1200" in html          # CO2 saved
    assert "2" in html and "3" in html  # reused / slots
    assert "co2" in html           # objective
    assert "optimal" in html.lower()  # proven-optimal badge


def test_render_lists_every_assignment_with_demand_and_donor():
    html = view.render_results_html(_SAMPLE)
    for token in ("N1", "W16X26", "D1", "W18X55", "N2", "D2", "W21X44"):
        assert token in html
    # None chi_lt renders as an em dash, not "None"
    assert "None" not in html
    assert "—" in html        # em dash for the missing chi_lt


def test_render_includes_filter_controls_and_js():
    html = view.render_results_html(_SAMPLE)
    # the three display filters + the toggling script
    assert "srx-filter-section" in html
    assert "srx-filter-status" in html
    assert "srx-filter-util" in html
    assert "srxFilter" in html
    # rows carry the data-* attributes the filter reads
    assert "srx-row" in html
    assert "data-util" in html and "data-status" in html and "data-section" in html


def test_render_shows_unfilled_and_quarantined_sections():
    html = view.render_results_html(_SAMPLE)
    assert "N3" in html            # unfilled demand
    assert "condition D" in html   # quarantine reason
    assert "REVIEW" in html        # per-assignment check status


def test_render_escapes_html_in_free_text():
    data = dict(_SAMPLE)
    data["quarantined_donors"] = [{"donor_id": "D9", "donor_section": "W8X10",
                                   "reason": "cracked <flange> & web"}]
    html = view.render_results_html(data)
    assert "&lt;flange&gt;" in html
    assert "&amp;" in html
    assert "<flange>" not in html
