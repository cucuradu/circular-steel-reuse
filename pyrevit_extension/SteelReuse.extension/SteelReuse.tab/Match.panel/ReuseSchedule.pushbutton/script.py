# -*- coding: utf-8 -*-
"""Create (or open) the "SteelReuse Passport" schedule: the reuse data as a native Revit schedule.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why).

Builds a multi-category schedule over the SteelReuse shared parameters that Apply Matches wrote
("Reuse Status", "Reuse Paired With", "Reuse CO2 Saved (kg)", "Reuse Note") plus Family and Type.
Filtered to elements that actually carry a reuse status, sorted by status, with a grand total on
the CO2 column -- the model's own "how much did reuse save" answer, live in Revit.

Run Apply Matches first (it creates the parameters and fills them); then this button.
Re-running just opens the existing schedule.
"""

from pyrevit import DB, forms, revit, script

output = script.get_output()
doc = revit.doc

SCHEDULE_NAME = "SteelReuse Passport"
# field display order: identity first, then the reuse story
FIELD_ORDER = ("Family and Type", "Reuse Status", "Reuse Paired With",
               "Reuse CO2 Saved (kg)", "Reuse Note")
PARAM_STATUS = "Reuse Status"
PARAM_CO2 = "Reuse CO2 Saved (kg)"


def _existing_schedule():
    for vs in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if vs.Name == SCHEDULE_NAME:
            return vs
    return None


def _open(view):
    try:
        revit.uidoc.ActiveView = view
    except Exception:
        try:
            revit.uidoc.RequestViewChange(view)
        except Exception:
            pass


def _params_bound():
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        if it.Key.Name == PARAM_STATUS:
            return True
    return False


def main():
    existing = _existing_schedule()
    if existing is not None:
        _open(existing)
        output.print_md("Opened the existing **%s** schedule." % SCHEDULE_NAME)
        return

    if not _params_bound():
        forms.alert("The '%s' parameter is not in this model yet.\n"
                    "Run Apply Matches first -- it creates and fills the reuse parameters."
                    % PARAM_STATUS)
        return

    with revit.Transaction("SteelReuse: Reuse Schedule"):
        # Multi-category schedule, so framing AND columns appear in one passport.
        schedule = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId.InvalidElementId)
        schedule.Name = SCHEDULE_NAME
        definition = schedule.Definition

        by_name = {}
        for sf in definition.GetSchedulableFields():
            try:
                by_name[sf.GetName(doc)] = sf
            except Exception:
                continue

        added = {}
        missing = []
        for name in FIELD_ORDER:
            sf = by_name.get(name)
            if sf is None:
                missing.append(name)
                continue
            added[name] = definition.AddField(sf)

        # Only rows that carry a reuse status (skip every untouched element).
        try:
            f = DB.ScheduleFilter(added[PARAM_STATUS].FieldId,
                                  DB.ScheduleFilterType.HasValue)
            definition.AddFilter(f)
        except Exception:
            pass  # older API: unfiltered schedule is still usable

        try:
            definition.AddSortGroupField(
                DB.ScheduleSortGroupField(added[PARAM_STATUS].FieldId))
        except Exception:
            pass

        # Grand total on the CO2 column: the headline number, computed by Revit from the values.
        try:
            co2_field = added.get(PARAM_CO2)
            if co2_field is not None:
                co2_field.DisplayType = DB.ScheduleFieldDisplayType.Totals
            definition.ShowGrandTotal = True
            definition.ShowGrandTotalTitle = True
        except Exception:
            pass

    _open(schedule)
    output.print_md("**Created the %s schedule** (%d column(s))." % (SCHEDULE_NAME, len(added)))
    if missing:
        output.print_md("Could not add: %s." % ", ".join(missing))


if __name__ == "__main__":
    main()
