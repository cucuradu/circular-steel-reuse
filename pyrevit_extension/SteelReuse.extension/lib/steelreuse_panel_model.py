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


# KPI rows compared between two runs: (display label, kpis-block key). The unfilled count is handled
# separately because it comes from the length of the 'unfilled' list, not the kpis block.
_DIFF_KPIS = [
    ("Members reused", "reused"),
    ("CO2e saved (kg)", "co2_saved_kg"),
    ("Mass reused (kg)", "mass_reused_kg"),
    ("Distinct sections", "distinct_sections"),
]


def _slot_donors(data):
    """Map slot_id -> donor_id for filled slots, slot_id -> None for unfilled slots."""
    out = {}
    for a in data.get("assignments", []):
        out[a.get("slot_id")] = a.get("donor_id")
    for u in data.get("unfilled", []):
        out[u.get("slot_id")] = None
    return out


def _delta(baseline, current):
    """current - baseline, rounded to 1 dp when either side is a float."""
    if isinstance(baseline, float) or isinstance(current, float):
        return round(current - baseline, 1)
    return current - baseline


def diff(baseline_data, current_data):
    """Compare two results.json v2 dicts: KPI deltas + per-slot outcome changes.

    Returns ``{"kpis": [{"label","baseline","current","delta"}, ...],
    "slots": [{"slot_id","change","detail"}, ...]}`` where ``change`` is 'lost' (was filled, now
    unfilled), 'gained' (was unfilled, now filled) or 'donor' (filled by a different member).
    Unchanged slots are omitted. Pure data in/out, so it is unit-tested headless.
    """
    bk = baseline_data.get("kpis", {})
    ck = current_data.get("kpis", {})
    kpis = []
    for label, key in _DIFF_KPIS:
        b = bk.get(key, 0)
        c = ck.get(key, 0)
        kpis.append({"label": label, "baseline": b, "current": c, "delta": _delta(b, c)})
    bu = len(baseline_data.get("unfilled", []))
    cu = len(current_data.get("unfilled", []))
    kpis.append({"label": "Unfilled slots", "baseline": bu, "current": cu, "delta": cu - bu})

    base = _slot_donors(baseline_data)
    cur = _slot_donors(current_data)
    slots = []
    for slot_id in sorted(set(base) | set(cur)):
        b = base.get(slot_id)
        c = cur.get(slot_id)
        if b == c:
            continue
        if b is not None and c is None:
            slots.append({"slot_id": slot_id, "change": "lost", "detail": "filled -> unfilled"})
        elif b is None and c is not None:
            slots.append({"slot_id": slot_id, "change": "gained",
                          "detail": "unfilled -> filled (" + str(c) + ")"})
        else:
            slots.append({"slot_id": slot_id, "change": "donor",
                          "detail": "donor " + str(b) + " -> " + str(c)})
    return {"kpis": kpis, "slots": slots}


def kpi_table(named_runs):
    """Compare N runs' KPIs side by side.

    ``named_runs = [(name, data), ...]`` (each ``data`` a results.json v2 dict). Returns
    ``{"columns": [name, ...], "rows": [{"label", "values": [...]}, ...]}`` over the same KPI set as
    :func:`diff` (members reused, CO2e saved, mass reused, distinct sections, unfilled count). Pure,
    so it is unit-tested headless.
    """
    columns = [name for name, _ in named_runs]
    rows = []
    for label, key in _DIFF_KPIS:
        rows.append({"label": label,
                     "values": [d.get("kpis", {}).get(key, 0) for _, d in named_runs]})
    rows.append({"label": "Unfilled slots",
                 "values": [len(d.get("unfilled", [])) for _, d in named_runs]})
    return {"columns": columns, "rows": rows}
