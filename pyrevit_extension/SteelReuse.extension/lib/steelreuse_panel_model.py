# -*- coding: utf-8 -*-
"""Headless view-model for the SteelReuse results panel: parse results.json v2 into display rows
and filter them.

No Revit, no WPF -> unit-testable under CPython exactly as it runs under IronPython in Revit.
IronPython-safe: stdlib only, no f-strings, no %-formatting. The panel UI (steelreuse_panel.py)
binds these plain objects to the WPF DataGrid; all the parsing/filtering logic lives here so it can
be tested without the host.
"""

SCHEMA_VERSION = 2


def _display_status(a):
    """The status chip the grid colours: 'review' if the check or connection needs a look, else
    'filled'. (Unfilled slots are not assignments -- they live in the separate 'unfilled' block.)"""
    if a.get("connection_review") or a.get("check_status") == "REVIEW":
        return "review"
    return "filled"


def _restraint_warn(a):
    """True when a beam passes bending only because the slab restrains the flange (chi_LT == 1.0 but
    would drop below 0.85 if unrestrained) -- the construction-stage LTB flag the report also shows."""
    chi = a.get("chi_lt")
    free = a.get("chi_lt_if_free")
    return chi == 1.0 and free is not None and free < 0.85


class Row:
    """One assignment row, flattened for the grid (plain attributes, no JSON dict lookups in XAML)."""

    __slots__ = ("slot_id", "demand_id", "demand_section", "donor_id", "donor_section",
                 "utilization", "governing", "status", "restraint_warn", "connection",
                 "offcut_mm", "co2_saved_kg", "verification", "condition")

    def __init__(self, a):
        self.slot_id = a.get("slot_id", "")
        self.demand_id = a.get("demand_id", "")
        self.demand_section = a.get("demand_section", "")
        self.donor_id = a.get("donor_id", "")
        self.donor_section = a.get("donor_section", "")
        self.utilization = a.get("utilization") or 0.0
        self.governing = a.get("governing_combo", "")
        self.status = _display_status(a)
        self.restraint_warn = _restraint_warn(a)
        self.connection = a.get("connection", "")
        self.offcut_mm = a.get("offcut_mm") or 0.0
        self.co2_saved_kg = a.get("co2_saved_kg") or 0.0
        self.verification = a.get("verification", "")
        self.condition = a.get("condition", "")


class ResultsView:
    """Parsed results.json v2: KPI/diagnosis/warnings blocks, assignment rows, and the optional
    portfolio/pareto/disposition/audit blocks (present only when that analysis ran)."""

    def __init__(self, data):
        self.schema_ok = data.get("schema_version") == SCHEMA_VERSION
        self.kpis = data.get("kpis", {})
        self.diagnosis = data.get("diagnosis", {})
        self.warnings = data.get("warnings", {})
        self.unfilled = data.get("unfilled", [])
        self.quarantined_donors = data.get("quarantined_donors", [])
        self.portfolio = data.get("portfolio", [])
        self.pareto = data.get("pareto", [])
        self.disposition = data.get("disposition", {})
        self.audit = data.get("audit", {})
        self.paths = data.get("paths", {})
        self.rows = [Row(a) for a in data.get("assignments", [])]

    @property
    def has_portfolio(self):
        return bool(self.portfolio)

    @property
    def has_pareto(self):
        return bool(self.pareto)

    @property
    def has_disposition(self):
        return bool(self.disposition)

    @property
    def has_audit(self):
        return bool(self.audit)


def parse(data):
    """Parse a decoded results.json dict into a :class:`ResultsView`."""
    return ResultsView(data)


def filter_rows(rows, status="all", section="", min_util=0.0):
    """Filter the assignment rows for the grid view (never re-runs the match).

    ``status`` 'all' or a chip value; ``section`` a case-insensitive substring matched against either
    the donor or demand section; ``min_util`` a governing-utilisation floor. All independent.
    """
    out = []
    needle = (section or "").strip().upper()
    for r in rows:
        if status and status != "all" and r.status != status:
            continue
        if needle and needle not in (r.donor_section + " " + r.demand_section).upper():
            continue
        if min_util and r.utilization < min_util:
            continue
        out.append(r)
    return out
