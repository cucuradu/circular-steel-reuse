# -*- coding: utf-8 -*-
"""Shared Apply-Matches button logic, parameterised by side (donor / demand).

Split out of the old single ApplyMatches button so the ribbon can offer "Apply to Donor" / "Apply to
Demand" as a split-button -- the side is chosen on the ribbon instead of in a popup after the click.
Both entry pushbuttons call :func:`run` with their fixed side. The colouring + parameter writing
itself lives in :mod:`steelreuse_apply` (shared with the SteelReuse window's Apply button).

IronPython-safe: stdlib + pyRevit only, no f-strings, %-formatting.
"""

import json
import os

import steelreuse_apply as apply_mod
import steelreuse_runner as runner
import steelreuse_runs as runhist
from pyrevit import forms, revit, script

MAX_LINKS = 25  # cap the clickable list per status so huge models stay readable
_PICK_FILE = "Pick a JSON file..."  # sentinel for the file-picker fallback in the run list

# lib/ -> SteelReuse.extension (works no matter how deep the calling pushbutton is nested).
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _pick_status_data():
    """Get the apply-matches status dict, either from a saved run (by name) or a picked JSON file.

    Saved runs (steelreuse_runs/) are listed by name so an *old* run can be re-applied, not just the
    last one. A run saved before apply-data archiving has no status to apply -- we say so and fall
    back to the file picker.
    """
    history_dir = runner.load_settings(_EXT_ROOT).get("history_dir")
    saved = runhist.load_runs(history_dir) if history_dir else []

    if saved:
        labels = {}
        for r in saved:
            label = "%s  --  %s  (%s)" % (r.get("name", "run"), r.get("params_label", ""),
                                          r.get("timestamp", ""))
            labels[label] = r
        choice = forms.SelectFromList.show(
            [_PICK_FILE] + list(labels.keys()),
            title="Apply which run? (saved runs by name, or pick a file)", button_name="Apply")
        if not choice:
            return None
        if choice != _PICK_FILE:
            run_entry = labels[choice]
            data = runhist.load_run_status(history_dir, run_entry.get("id"))
            if data is None:
                forms.alert("'%s' was saved before apply-data archiving, so it can't be applied. "
                            "Re-run it, or pick a status.json file." % run_entry.get("name", "run"),
                            title="SteelReuse")
                # fall through to the file picker
            else:
                return data

    json_path = forms.pick_file(file_ext="json", title="Pick the SteelReuse apply-matches JSON")
    if not json_path:
        return None
    with open(json_path) as fh:
        return json.load(fh)


def run(side):
    """Apply the picked run's ``side`` (``"donor"`` or ``"demand"``) statuses to the active model."""
    output = script.get_output()
    doc = revit.doc

    data = _pick_status_data()
    if data is None:
        output.print_md("_No run selected — nothing applied._")
        return
    statuses = data.get(side, {})
    if not statuses:
        forms.alert("No '%s' entries in this JSON.\n\nThis run may be for the other side." % side,
                    title="SteelReuse")
        return

    view = doc.ActiveView
    result = apply_mod.apply_matches(doc, view, statuses, side)

    # Headline numbers (computed by the external pipeline; just printed here).
    summary = data.get("summary") or {}
    if summary:
        output.print_md("**Run summary**: %s of %s slot(s) filled by reuse | %.0f kg CO2e saved "
                        "| %s donor member(s) in stock."
                        % (summary.get("n_reused", "?"), summary.get("slot_count", "?"),
                           summary.get("co2_saved_kg", 0.0), summary.get("supply_count", "?")))

    output.print_md("**Applied %d %s element(s)** in view '%s' (%d id(s) not found in this model)."
                    % (result["applied"], side, view.Name, result["missing"]))
    for status, count in sorted(result["by_status"].items()):
        output.print_md("- %s: %d" % (status, count))

    # Clickable lists for the statuses that need a human decision (click = select + zoom).
    for status in apply_mod.ATTENTION_STATUSES.get(side, ()):
        ids = result["attention"].get(status, [])
        if not ids:
            continue
        shown = ids[:MAX_LINKS]
        links = " ".join(output.linkify(i) for i in shown)
        more = " ... +%d more" % (len(ids) - len(shown)) if len(ids) > len(shown) else ""
        output.print_md("**%s** (%d): %s%s" % (status, len(ids), links, more))

    output.print_md("Reuse data written to the '%s' parameters -- use **Reuse Schedule** to see "
                    "the passport as a native Revit schedule." % apply_mod.SP_GROUP)
