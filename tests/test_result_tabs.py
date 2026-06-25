"""Tests for the shared review-tab formatters (lib/steelreuse_result_tabs.py).

Pure text formatting of a parsed ResultsView -- the same bodies the Run Match and Results windows
bind to their tabs -- so it runs under CPython here exactly as under IronPython in Revit.
"""

import importlib
import os
import sys

_LIBDIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pyrevit_extension",
                                       "SteelReuse.extension", "lib"))
# Put lib/ on the path so the modules' bare cross-imports (e.g. result_tabs -> panel_model) resolve,
# exactly as pyRevit arranges them in Revit.
if _LIBDIR not in sys.path:
    sys.path.insert(0, _LIBDIR)

model = importlib.import_module("steelreuse_panel_model")
tabs = importlib.import_module("steelreuse_result_tabs")


_SAMPLE = {
    "schema_version": 2,
    "kpis": {"slots": 3, "reused": 2},
    "assignments": [
        {"slot_id": "N1#0", "donor_section": "IPE300", "utilization": 0.6, "co2_saved_kg": 100.0,
         "offcut_mm": 500.0, "check_status": "OK"},
        {"slot_id": "N2#0", "donor_section": "IPE300", "utilization": 0.9, "co2_saved_kg": 200.0,
         "offcut_mm": 0.0, "check_status": "OK"},
    ],
    "unfilled": [{"slot_id": "N3#0", "demand_section": "IPE200",
                  "reason_detail": "too short for any donor"}],
    "diagnosis": {"binding_constraint": "length", "lever": "splice or source longer stock",
                  "n_unmatched": 1},
    "warnings": {"ltb_restraint_reliant": 1, "imperfection_governed": 0, "connection_review": 2,
                 "cut_donors": 1, "reusable_remainder_m": 3.5, "unknown": 4,
                 "unknown_breakdown": [{"name": "BAR JOIST", "count": 4}]},
}


def _view(extra=None):
    data = dict(_SAMPLE)
    if extra:
        data.update(extra)
    return model.parse(data)


def test_diagnosis_and_unfilled_render_the_reasons():
    v = _view()
    assert "Binding constraint: length" in tabs.diagnosis(v)
    u = tabs.unfilled(v)
    assert "1 unfilled slot(s)" in u and "N3#0" in u and "too short for any donor" in u


def test_diagnosis_all_filled_note():
    v = _view({"diagnosis": {"binding_constraint": "none"}})
    assert "Every demand slot that could be filled was filled." == tabs.diagnosis(v)


def test_rollup_groups_by_section():
    out = tabs.rollup(_view())
    assert "IPE300" in out and "section" in out   # both assignments roll into one IPE300 row


def test_warnings_lists_flags_and_breakdown():
    out = tabs.warnings(_view())
    assert "LTB restraint-reliant beams : 1" in out
    assert "Connection-review flags     : 2" in out
    assert "BAR JOIST" in out


def test_optional_tabs_render_when_present():
    v = _view({
        "disposition": {"totals": {"n": 2, "store": 1, "reroll": 1, "recycle": 0,
                                   "reroll_credit_kg": 12.0, "recycle_credit_kg": 8.0,
                                   "by_reason": {"too-short": 1, "too-weak": 0,
                                                 "contention": 1, "uneconomic": 0}},
                        "by_section": [{"section": "IPE300", "n": 2, "store": 1,
                                        "reroll": 1, "recycle": 0}]},
        "marginal_value": [{"supply_id": "D1", "section": "IPE300", "marginal_co2_kg": 120.5,
                            "slots_lost": ["N1#0"], "reshuffled_slots": 1}],
        "pareto": [{"objective": "co2", "label": "co2", "n_reused": 2, "co2_saved_kg": 300.0,
                    "mass_reused_kg": 500.0, "proven_optimal": True, "selected": True}],
        "portfolio": [{"tag": "blockA", "slot_count": 3, "n_reused": 2, "co2_saved_kg": 300.0,
                       "n_unmatched": 1}],
        "audit": {"audited": 5, "admitted": 4, "quarantined": 1, "avg_knockdown": 0.92,
                  "verification": [{"basis": "mill cert", "count": 4}],
                  "condition": [{"grade": "B", "count": 4}],
                  "quarantined_list": [{"id": "D9", "reason": "unverified"}]},
    })
    assert v.has_disposition and "Why unused: too-short 1" in tabs.disposition(v)
    assert v.has_marginal_value and "120.5" in tabs.marginal(v)
    assert v.has_pareto and "co2" in tabs.pareto(v)
    assert v.has_portfolio and "blockA" in tabs.portfolio(v)
    assert v.has_audit and "unverified" in tabs.audit(v)


def test_pareto_shows_deltas_vs_shipped_objective_and_column_winners():
    # co2 is the shipped objective (baseline); members gains a slot but costs CO2; mass wins on mass.
    v = _view({"pareto": [
        {"objective": "co2", "label": "co2", "n_reused": 2, "co2_saved_kg": 300.0,
         "mass_reused_kg": 500.0, "proven_optimal": True, "selected": True},
        {"objective": "members", "label": "members", "n_reused": 3, "co2_saved_kg": 280.0,
         "mass_reused_kg": 460.0, "proven_optimal": True, "selected": False},
        {"objective": "mass", "label": "mass", "n_reused": 2, "co2_saved_kg": 295.0,
         "mass_reused_kg": 520.0, "proven_optimal": False, "selected": False},
    ]})
    out = tabs.pareto(v)
    # The shipped row is the baseline: no parenthetical change on its own values.
    assert "*co2" in out
    # Switching to members buys +1 slot at a -20.0 kg CO2 cost; mass costs -40.0 kg of reused steel.
    assert "(+1)" in out and "(-20.0)" in out and "(-40.0)" in out
    # Column winners flagged with '#' (after any delta): members on reused, co2 on CO2e, mass on mass.
    assert "3 (+1) #" in out and "300.0 #" in out and "520.0 (+20.0) #" in out
    # 'mass' was not proven optimal in this fixture.
    assert "no" in out
