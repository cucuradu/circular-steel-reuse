# -*- coding: utf-8 -*-
"""Create (or open) a SteelReuse schedule: Passport (match results) or Value Case (reuse/scrap).

Split out of the old ReuseSchedule button so the ribbon offers "Passport" / "Value Case" as a
split-button instead of asking in a popup. Both entry pushbuttons call :func:`run` with their kind.

Builds a multi-category schedule over the SteelReuse shared parameters that Apply Matches or Value
Case wrote, plus Family and Type, filtered to elements that carry data, sorted and totalled.

IronPython-safe: stdlib + Revit/pyRevit only, no f-strings.
"""

from pyrevit import DB, forms, revit, script

PASSPORT = "Passport"
VALUE_CASE = "Value Case"

_SCHEDULE_NAMES = {
    PASSPORT: "SteelReuse Passport",
    VALUE_CASE: "SteelReuse Value Case",
}
_FIELD_ORDERS = {
    PASSPORT: ("Family and Type", "Reuse Status", "Reuse Paired With",
               "Reuse CO2 Saved (kg)", "Reuse Note"),
    VALUE_CASE: ("Family and Type", "Reuse VC Verdict", "Reuse VC Verification",
                 "Reuse VC Reclaimed Value (GBP)", "Reuse VC Premium (GBP)",
                 "Reuse VC CO2 Saved (kg)", "Reuse VC Note"),
}
_FILTER_PARAMS = {PASSPORT: "Reuse Status", VALUE_CASE: "Reuse VC Verdict"}
_SORT_PARAMS = {PASSPORT: "Reuse Status", VALUE_CASE: "Reuse VC Verdict"}
_TOTAL_PARAMS = {PASSPORT: "Reuse CO2 Saved (kg)", VALUE_CASE: "Reuse VC Premium (GBP)"}


def _existing_schedule(doc, name):
    for vs in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if vs.Name == name:
            return vs
    return None


def _open(view):
    try:
        revit.uidoc.ActiveView = view
    except Exception:  # noqa: BLE001 -- some view kinds can't be the active view; try a soft request
        try:
            revit.uidoc.RequestViewChange(view)
        except Exception:  # noqa: BLE001 -- non-fatal: the schedule still exists in the browser
            pass


def _params_bound(doc, param_name):
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        if it.Key.Name == param_name:
            return True
    return False


def _create_schedule(doc, kind):
    schedule_name = _SCHEDULE_NAMES[kind]
    field_order = _FIELD_ORDERS[kind]
    filter_param = _FILTER_PARAMS[kind]
    sort_param = _SORT_PARAMS[kind]
    total_param = _TOTAL_PARAMS[kind]

    with revit.Transaction("SteelReuse: %s Schedule" % kind):
        schedule = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId.InvalidElementId)
        schedule.Name = schedule_name
        definition = schedule.Definition

        by_name = {}
        for sf in definition.GetSchedulableFields():
            try:
                by_name[sf.GetName(doc)] = sf
            except Exception:  # noqa: BLE001 -- some fields raise on GetName; skip them
                continue

        added = {}
        missing = []
        for name in field_order:
            sf = by_name.get(name)
            if sf is None:
                missing.append(name)
                continue
            added[name] = definition.AddField(sf)

        try:
            f = DB.ScheduleFilter(added[filter_param].FieldId, DB.ScheduleFilterType.HasValue)
            definition.AddFilter(f)
        except Exception:  # noqa: BLE001 -- filter field absent -> show all rows
            pass

        try:
            definition.AddSortGroupField(DB.ScheduleSortGroupField(added[sort_param].FieldId))
        except Exception:  # noqa: BLE001 -- sort field absent -> default order
            pass

        try:
            tot_field = added.get(total_param)
            if tot_field is not None:
                tot_field.DisplayType = DB.ScheduleFieldDisplayType.Totals
            definition.ShowGrandTotal = True
            definition.ShowGrandTotalTitle = True
        except Exception:  # noqa: BLE001 -- totals optional
            pass

    return schedule, added, missing


def run(kind):
    """Open the existing ``kind`` schedule, or create it (after checking the params exist)."""
    output = script.get_output()
    doc = revit.doc

    schedule_name = _SCHEDULE_NAMES[kind]
    existing = _existing_schedule(doc, schedule_name)
    if existing is not None:
        _open(existing)
        output.print_md("Opened the existing **%s** schedule." % schedule_name)
        return

    filter_param = _FILTER_PARAMS[kind]
    if not _params_bound(doc, filter_param):
        run_first = "Apply Matches" if kind == PASSPORT else "Value Case"
        forms.alert(
            "The '%s' parameter is not in this model yet.\n"
            "Run %s first -- it creates and fills the reuse parameters." % (filter_param, run_first),
            title="SteelReuse")
        return

    schedule, added, missing = _create_schedule(doc, kind)
    _open(schedule)
    output.print_md("**Created the %s schedule** (%d column(s))." % (schedule_name, len(added)))
    if missing:
        output.print_md("Could not add: %s." % ", ".join(missing))
