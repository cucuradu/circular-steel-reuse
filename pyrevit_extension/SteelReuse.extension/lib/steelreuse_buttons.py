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
    return forms.pick_file(file_ext="json|csv|xlsx", title=title)


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
