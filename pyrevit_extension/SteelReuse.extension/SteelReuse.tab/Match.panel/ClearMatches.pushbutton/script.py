# -*- coding: utf-8 -*-
"""Undo a SteelReuse "Apply Matches" run: reset the colour overrides and clear the reuse data.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why).

Only touches elements Apply Matches marked: structural framing/columns with a non-empty
"Reuse Status" parameter (or, from older runs, a "Comments" starting with ``SteelReuse:``).
For each, the graphic override in the ACTIVE view is reset (an empty OverrideGraphicSettings
restores the category/view defaults) and the SteelReuse parameters / legacy comment are cleared.
All other overrides, parameters and comments in the model are left untouched. (The parameter
BINDINGS stay -- the columns remain available for schedules; only the values are emptied.)

Note: Apply Matches sets overrides per view -- run this in the same view you applied them in (or
re-run it per coloured view).
"""

from pyrevit import DB, forms, revit, script

output = script.get_output()
doc = revit.doc

LEGACY_MARKER = "SteelReuse:"
PARAM_STATUS = "Reuse Status"
TEXT_PARAMS = (PARAM_STATUS, "Reuse Note", "Reuse Paired With")
PARAM_CO2 = "Reuse CO2 Saved (kg)"


def _collect(category):
    return (DB.FilteredElementCollector(doc)
            .OfCategory(category)
            .WhereElementIsNotElementType()
            .ToElements())


def _text(elem, name):
    p = elem.LookupParameter(name)
    if p is not None and p.HasValue:
        return p.AsString()
    return None


def _clear_text(elem, name):
    p = elem.LookupParameter(name)
    if p is not None and not p.IsReadOnly and p.HasValue:
        p.Set("")


def main():
    elems = list(_collect(DB.BuiltInCategory.OST_StructuralFraming))
    elems += list(_collect(DB.BuiltInCategory.OST_StructuralColumns))

    view = doc.ActiveView
    reset = DB.OverrideGraphicSettings()  # empty = back to category/view defaults

    cleared = 0
    with revit.Transaction("SteelReuse: Clear Matches"):
        for elem in elems:
            comment = _text(elem, "Comments")
            legacy = bool(comment) and comment.startswith(LEGACY_MARKER)
            marked = bool(_text(elem, PARAM_STATUS)) or legacy
            if not marked:
                continue
            try:
                view.SetElementOverrides(elem.Id, reset)
            except Exception:
                pass  # view may not support overrides; still clear the data
            for name in TEXT_PARAMS:
                _clear_text(elem, name)
            p = elem.LookupParameter(PARAM_CO2)
            if p is not None and not p.IsReadOnly and p.HasValue:
                p.Set(0.0)
            if legacy:
                _clear_text(elem, "Comments")
            cleared += 1

    if cleared:
        output.print_md("**Cleared %d element(s)** in view '%s' (override reset, reuse data "
                        "removed)." % (cleared, view.Name))
    else:
        forms.alert("No elements marked by Apply Matches found in this model.\n"
                    "Nothing was changed.")


if __name__ == "__main__":
    main()
