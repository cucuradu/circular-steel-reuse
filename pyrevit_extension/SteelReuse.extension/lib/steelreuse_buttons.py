# -*- coding: utf-8 -*-
"""Shared preamble for the buttons that shell the CPython engine on a donor model.

Six buttons (Value Case, Review Extraction, PDA Report, Highlight Problems, Audit Grid, Import
Survey) all need the same things before they can do anything: a working Python interpreter and a
donor.json. Three of them additionally run the SAME extraction review. Centralising both here means
no button fails silently and one fresh review is shared instead of re-spawning the engine per click.

IronPython-safe: stdlib + pyRevit only, no f-strings, %-formatting.
"""

import json
import os

import steelreuse_runner as runner
from pyrevit import forms

# The extension root, derived from this lib module's own location (lib/ sits directly under it). Using
# this frees buttons from computing "../../.." off their own path, so they work at any nesting depth
# (a plain .pushbutton or one inside a .pulldown / .splitbutton).
EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# The model/inventory inputs every picker accepts: the extractor's .json or a .csv/.xlsx inventory.
_MODEL_EXTS = ("json", "csv", "xlsx")


def _ext_filter(exts):
    """A common-file-dialog filter string for ``exts`` (e.g. ('json','csv','xlsx')).

    Description|Pattern pairs, the format both WinForms and WPF (Microsoft.Win32) dialogs expect, with
    every extension in one pattern so all of them show at once plus an "All files" escape hatch.
    """
    pats = ";".join("*." + e for e in exts)
    return "Supported (%s)|%s|All files (*.*)|*.*" % (pats, pats)


def pick_model_file(title, multi_file=False, exts=_MODEL_EXTS, owner=None):
    """Pick a model/inventory file with the WPF dialog (Microsoft.Win32), NOT pyRevit's WinForms one.

    Why not ``forms.pick_file``: its WinForms ``OpenFileDialog`` with ``Multiselect=True``, shown from
    the modeless Run Match WPF window under Revit 2026, throws a managed exception *inside its own
    modal message pump* (0xe0434352) -- a hard Revit crash that NO surrounding try/except can catch,
    because the throw never unwinds through our call frame (confirmed in journal.0042: a fatal error
    on the demand click with no Python traceback, while the single-select donor dialog worked). The
    WPF ``Microsoft.Win32.OpenFileDialog`` is the dialog meant for a WPF app, supports ``Multiselect``,
    and runs cleanly in the window's own dispatcher.

    Returns a path, a list of paths (``multi_file``), or None on cancel/error. ``owner`` (the WPF
    window) parents the dialog when given. The try/except is belt-and-braces for the catchable errors
    (a bad assembly load, etc.); the real fix is the dialog swap.
    """
    try:
        import clr
        clr.AddReference("PresentationFramework")  # where Microsoft.Win32.OpenFileDialog lives
        from Microsoft.Win32 import OpenFileDialog
        dlg = OpenFileDialog()
        dlg.Title = title
        dlg.Filter = _ext_filter(exts)
        dlg.Multiselect = bool(multi_file)
        ok = dlg.ShowDialog(owner) if owner is not None else dlg.ShowDialog()
        if not ok:  # ShowDialog returns Nullable<bool>: False/None both mean cancelled
            return None
        return list(dlg.FileNames) if multi_file else dlg.FileName
    except Exception as ex:  # noqa: BLE001 -- never let a picker error crash Revit
        forms.alert("Could not open the file picker:\n\n%s" % ex, title="SteelReuse")
        return None


def resolve_interpreter(ext_root, alert=True):
    """The CPython interpreter to drive the engine, or None (after an alert) if none works."""
    interp = runner.discover_interpreter(runner.load_settings(ext_root).get("interpreter"), ext_root)
    if not interp and alert:
        forms.alert("No working Python interpreter found.\n\nOpen Run Match once to locate the "
                    "signed venv, then try again.", title="SteelReuse")
    return interp


def resolve_donor(ext_root, title="Select the donor model or inventory"):
    """The last donor model if it still exists, else ask the user to pick one (None if cancelled).

    Accepts the extractor's .json or a .csv/.xlsx inventory spreadsheet (the engine dispatches by
    extension), so the donor-only buttons (Value Case, Review, PDA) take a no-Revit inventory too.
    """
    donor = runner.load_settings(ext_root).get("last_donor")
    if donor and os.path.isfile(donor):
        return donor
    return pick_model_file(title)


def interpreter_and_donor(ext_root, title="Select the extracted donor.json"):
    """(interpreter, donor); either may be None. Alerts when the interpreter is missing.

    The single entry point every engine-on-donor button should use for its preamble.
    """
    interp = resolve_interpreter(ext_root)
    if not interp:
        return None, None
    return interp, resolve_donor(ext_root, title)


def review_or_reuse(ext_root, interp, donor):
    """Return ``(review_dict, error)``: the extraction review for ``donor``, reusing a fresh cached
    ``review.json`` instead of re-running the engine.

    The cache is reused only when it was built from THIS donor (tracked in settings) and is newer
    than the donor file -- so Review Problems / PDA / Highlight share one run. On failure ``review``
    is None and ``error`` is the runner result dict (for the caller's alert); on success ``error`` is
    None.
    """
    out_dir = runner.reports_dir(ext_root)
    review_json = runner.review_paths(out_dir)["review_json"]
    settings = runner.load_settings(ext_root)
    fresh = (os.path.isfile(review_json) and os.path.isfile(donor)
             and settings.get("last_review_donor") == donor
             and os.path.getmtime(review_json) >= os.path.getmtime(donor))
    if not fresh:
        res = runner.run_review(interp, {"donor": donor}, out_dir)
        if not res["ok"]:
            return None, res
        runner.save_settings(ext_root, dict(settings, last_review_donor=donor))
    with open(review_json, encoding="utf-8") as handle:
        return json.load(handle), None
