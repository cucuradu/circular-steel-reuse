# -*- coding: utf-8 -*-
"""Undo everything SteelReuse drew on the model, in one action.

Replaces the old separate Clear Matches + Clear Highlights buttons (the user asked for "just a
Clear"). In the active view it resets the graphic overrides AND clears the passport data on every
framing/column that ANY SteelReuse action coloured -- so it never leaves stale green behind:
  * Apply Matches      -> detected by a non-empty "Reuse Status" (or a legacy ``SteelReuse:`` comment);
  * Value Case         -> detected by a non-empty "Reuse VC Verdict" (its own param set, NOT
    "Reuse Status" -- so the old Clear missed it and left value-case colours on the model);
  * Highlight Problems -> detected via the saved highlight id list (it writes no param), which is now
    accumulated across runs so a second highlight never orphans the first.
The parameter BINDINGS are left in place so schedules keep their columns; only the values are cleared.
Finally the saved highlight state is forgotten.

IronPython-safe: stdlib + Revit/pyRevit only, no f-strings.
"""

import steelreuse_buttons as buttons
import steelreuse_runner as runner
from pyrevit import DB, forms, revit, script

LEGACY_MARKER = "SteelReuse:"
PARAM_STATUS = "Reuse Status"
TEXT_PARAMS = (PARAM_STATUS, "Reuse Note", "Reuse Paired With")
PARAM_CO2 = "Reuse CO2 Saved (kg)"
# Value Case writes its OWN parameter set (steelreuse_apply.apply_value_case); Clear must recognise and
# wipe these too, else value-case colours survive a Clear.
PARAM_VC_VERDICT = "Reuse VC Verdict"
VC_TEXT_PARAMS = (PARAM_VC_VERDICT, "Reuse VC Verification", "Reuse VC Note")
VC_NUMBER_PARAMS = ("Reuse VC Reclaimed Value (GBP)", "Reuse VC Premium (GBP)",
                    "Reuse VC CO2 Saved (kg)")


def _collect(doc, category):
    return (DB.FilteredElementCollector(doc)
            .OfCategory(category)
            .WhereElementIsNotElementType()
            .ToElements())


def _idval(element_id):
    """ElementId's integer value across API generations (.Value is the 2024+ way)."""
    try:
        return int(element_id.Value)
    except Exception:  # noqa: BLE001 -- older API exposes IntegerValue instead
        return int(element_id.IntegerValue)


def _text(elem, name):
    p = elem.LookupParameter(name)
    if p is not None and p.HasValue:
        return p.AsString()
    return None


def _clear_text(elem, name):
    p = elem.LookupParameter(name)
    if p is not None and not p.IsReadOnly and p.HasValue:
        p.Set("")


def _clear_number(elem, name):
    p = elem.LookupParameter(name)
    if p is not None and not p.IsReadOnly and p.HasValue:
        p.Set(0.0)


def run():
    output = script.get_output()
    doc = revit.doc
    ext_root = buttons.EXT_ROOT

    highlight_ids = set(runner.load_highlight(ext_root)
                        or runner.load_settings(ext_root).get("highlighted_ids", []))

    elems = list(_collect(doc, DB.BuiltInCategory.OST_StructuralFraming))
    elems += list(_collect(doc, DB.BuiltInCategory.OST_StructuralColumns))

    view = doc.ActiveView
    reset = DB.OverrideGraphicSettings()  # empty = back to category/view defaults
    cleared_data = 0
    reset_overrides = 0
    with revit.Transaction("SteelReuse: Clear"):
        for elem in elems:
            comment = _text(elem, "Comments")
            legacy = bool(comment) and comment.startswith(LEGACY_MARKER)
            status_marked = bool(_text(elem, PARAM_STATUS)) or legacy
            vc_marked = bool(_text(elem, PARAM_VC_VERDICT))
            highlighted = str(_idval(elem.Id)) in highlight_ids
            if not (status_marked or vc_marked or highlighted):
                continue
            try:
                view.SetElementOverrides(elem.Id, reset)
                reset_overrides += 1
            except Exception:  # noqa: BLE001 -- view may not support overrides; still clear the data
                pass
            if status_marked:
                for name in TEXT_PARAMS:
                    _clear_text(elem, name)
                _clear_number(elem, PARAM_CO2)
                if legacy:
                    _clear_text(elem, "Comments")
            if vc_marked:
                for name in VC_TEXT_PARAMS:
                    _clear_text(elem, name)
                for name in VC_NUMBER_PARAMS:
                    _clear_number(elem, name)
            if status_marked or vc_marked:
                cleared_data += 1

    # Forget the saved highlight state (both the new file and any legacy settings key).
    runner.save_highlight(ext_root, [])
    settings = runner.load_settings(ext_root)
    if "highlighted_ids" in settings:
        del settings["highlighted_ids"]
        runner.save_settings(ext_root, settings)

    if reset_overrides or cleared_data:
        output.print_md("**Cleared** in view '%s': %d override(s) reset, reuse data removed from %d "
                        "element(s)." % (view.Name, reset_overrides, cleared_data))
    else:
        forms.alert("Nothing to clear: no SteelReuse colours, data, or highlights in this model.",
                    title="SteelReuse")
