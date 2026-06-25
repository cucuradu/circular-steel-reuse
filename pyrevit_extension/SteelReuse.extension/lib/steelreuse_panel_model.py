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


def _util_severity(u):
    """Coarse utilisation band for the grid's colour cue: '' / low / ok / high / over (>1.0)."""
    if u is None:
        return ""
    if u > 1.0:
        return "over"
    if u >= 0.85:
        return "high"
    if u >= 0.5:
        return "ok"
    return "low"


def _alt_display(a):
    """Tier 3 'next best' cell: the runner-up donor for this slot, its net-CO2 margin, and (when it
    was reused elsewhere) why it did not take this slot. '' when no substitute existed."""
    alt = a.get("alt_donor_id")
    if not alt:
        return ""
    txt = str(alt)
    margin = a.get("alt_margin_kg")
    if margin is not None:
        txt += " (margin " + str(margin) + " kg)"
    if a.get("alt_used_elsewhere"):
        txt += " - used elsewhere"
    return txt


class Row:
    """One assignment row, flattened for the grid (plain attributes, no JSON dict lookups in XAML)."""

    __slots__ = ("slot_id", "demand_id", "demand_section", "donor_id", "donor_section",
                 "utilization", "governing", "status", "restraint_warn", "connection",
                 "offcut_mm", "co2_saved_kg", "verification", "condition", "alt_display",
                 "alt_donor_id", "alt_margin_kg", "alt_used_elsewhere", "util_severity")

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
        self.alt_display = _alt_display(a)
        # Tier 3 next-best (raw fields for filtering + CSV export, alongside the display string).
        self.alt_donor_id = a.get("alt_donor_id") or ""
        self.alt_margin_kg = a.get("alt_margin_kg")
        self.alt_used_elsewhere = bool(a.get("alt_used_elsewhere"))
        self.util_severity = _util_severity(self.utilization)


class MismatchRow:
    """One donor-row provenance entry, flattened for a WPF DataGrid (plain attributes, not dict keys
    -- WPF binds to properties). Mirrors the mismatch log embedded in results.json (Roadmap §1.2)."""

    __slots__ = ("donor_id", "raw_section", "section", "classification", "outcome", "reason")

    def __init__(self, r):
        self.donor_id = r.get("id", "")
        self.raw_section = r.get("raw_section", "")
        self.section = r.get("canonical") or ""
        self.classification = r.get("classification", "")
        self.outcome = r.get("outcome", "")
        self.reason = r.get("reason", "")


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
        # Tier 4: per-donor what-if marginal value (one row per reused donor); present only when the
        # run was launched with --donor-value (one extra MILP solve per donor).
        self.marginal_value = data.get("marginal_value", [])
        self.audit = data.get("audit", {})
        self.paths = data.get("paths", {})
        # Roadmap §1.2: the externalised rule-data versions + the donor-row mismatch log
        # (classification + reason per donor). Both optional (older runs predate them).
        self.rules = data.get("rules", {})
        self.mismatch = data.get("mismatch", {})
        self.rows = [Row(a) for a in data.get("assignments", [])]
        # Donor-provenance rows as bindable objects for the Results window's Provenance grid.
        self.mismatch_rows = [MismatchRow(r) for r in self.mismatch.get("rows", [])]

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
    def has_marginal_value(self):
        return bool(self.marginal_value)

    @property
    def has_audit(self):
        return bool(self.audit)

    @property
    def has_mismatch(self):
        return bool(self.mismatch.get("rows"))


def parse(data):
    """Parse a decoded results.json dict into a :class:`ResultsView`."""
    return ResultsView(data)


def filter_rows(rows, status="all", section="", min_util=0.0):
    """Filter the assignment rows for the grid view (never re-runs the match).

    ``status`` 'all', a chip value ('filled'/'review'), or 'contention' (only rows whose next-best
    donor was used elsewhere); ``section`` a case-insensitive substring matched against either the
    donor or demand section; ``min_util`` a governing-utilisation floor. All independent.
    """
    out = []
    needle = (section or "").strip().upper()
    for r in rows:
        if status and status != "all":
            if status == "contention":
                if not r.alt_used_elsewhere:
                    continue
            elif r.status != status:
                continue
        if needle and needle not in (r.donor_section + " " + r.demand_section).upper():
            continue
        if min_util and r.utilization < min_util:
            continue
        out.append(r)
    return out


def section_rollup(rows):
    """Roll the assignment rows up by donor section: count, total CO2e saved, mean utilisation and
    total off-cut per section, ordered by CO2e saved (descending). Pure; for the 'By section' tab."""
    groups = {}
    order = []
    for r in rows:
        sec = r.donor_section or "(unmapped)"
        g = groups.get(sec)
        if g is None:
            g = groups[sec] = {"section": sec, "n": 0, "co2": 0.0, "offcut_mm": 0.0,
                               "util_sum": 0.0, "util_n": 0}
            order.append(sec)
        g["n"] += 1
        g["co2"] += r.co2_saved_kg or 0.0
        g["offcut_mm"] += r.offcut_mm or 0.0
        if r.utilization is not None:
            g["util_sum"] += r.utilization
            g["util_n"] += 1
    result = []
    for sec in order:
        g = groups[sec]
        g["mean_util"] = (g["util_sum"] / g["util_n"]) if g["util_n"] else 0.0
        g["offcut_m"] = g["offcut_mm"] / 1000.0
        result.append(g)
    result.sort(key=lambda g: -g["co2"])
    return result


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


def _slot_demand_ids(*datasets):
    """Map slot_id -> demand element id across the given results dicts (assignments + unfilled).

    The demand id is the same in both runs (it is the new-design member the slot represents), so the
    Compare window can zoom to a changed slot's demand member even when the slot went from filled to
    unfilled. Later datasets win, but the value is stable so order does not matter in practice.
    """
    out = {}
    for data in datasets:
        for a in data.get("assignments", []):
            if a.get("demand_id"):
                out[a.get("slot_id")] = a.get("demand_id")
        for u in data.get("unfilled", []):
            if u.get("demand_id"):
                out[u.get("slot_id")] = u.get("demand_id")
    return out


def _delta(baseline, current):
    """current - baseline, rounded to 1 dp when either side is a float."""
    if isinstance(baseline, float) or isinstance(current, float):
        return round(current - baseline, 1)
    return current - baseline


def diff(baseline_data, current_data):
    """Compare two results.json v2 dicts: KPI deltas + per-slot outcome changes.

    Returns ``{"kpis": [{"label","baseline","current","delta"}, ...],
    "slots": [{"slot_id","demand_id","change","donor_baseline","donor_current","detail"}, ...]}``
    where ``change`` is 'lost' (was filled, now unfilled), 'gained' (was unfilled, now filled) or
    'donor' (filled by a different member). ``demand_id``/``donor_*`` are the element ids the Compare
    window zooms to (donor ids are None when that side is unfilled). Unchanged slots are omitted.
    Pure data in/out, so it is unit-tested headless.
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
    demand_ids = _slot_demand_ids(baseline_data, current_data)
    slots = []
    for slot_id in sorted(set(base) | set(cur)):
        b = base.get(slot_id)
        c = cur.get(slot_id)
        if b == c:
            continue
        if b is not None and c is None:
            change, detail = "lost", "filled -> unfilled"
        elif b is None and c is not None:
            change, detail = "gained", "unfilled -> filled (" + str(c) + ")"
        else:
            change, detail = "donor", "donor " + str(b) + " -> " + str(c)
        slots.append({"slot_id": slot_id, "demand_id": demand_ids.get(slot_id),
                      "change": change, "donor_baseline": b, "donor_current": c, "detail": detail})
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
