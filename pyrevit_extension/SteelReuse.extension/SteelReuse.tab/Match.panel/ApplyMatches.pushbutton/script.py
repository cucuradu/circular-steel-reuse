# -*- coding: utf-8 -*-
"""Colour the active model's elements by their reuse status from a SteelReuse match run.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why).
Stdlib only (json), no f-strings, %-formatting.

Reads the per-element status JSON written by ``steelreuse --apply-matches-out status.json``
(:func:`steelreuse.writeback.build_writeback`):
``{"donor": {element_id: {status, color, note, paired_with, co2_saved_kg}}, "demand": {...},
"summary": {...}}``.

The colouring + reuse-passport parameter writing lives in the shared ``steelreuse_apply`` lib (so the
SteelReuse window's Apply button runs the same code); this button is the modal UI around it: pick the
JSON, choose which side the open model is, then print the run summary + clickable element links.
"""

import json
import os

import steelreuse_apply as apply_mod  # noqa: E402 -- extension lib/ is on the path
import steelreuse_runner as runner  # noqa: E402
import steelreuse_runs as runhist  # noqa: E402
from pyrevit import forms, revit, script

output = script.get_output()
doc = revit.doc

MAX_LINKS = 25  # cap the clickable list per status so huge models stay readable
_PICK_FILE = "Pick a JSON file..."  # sentinel for the file-picker fallback in the run list


def _pick_status_data():
    """Get the apply-matches status dict, either from a saved run (by name) or a picked JSON file.

    Saved runs (steelreuse_runs/) are listed by name so an *old* run can be re-applied, not just the
    last one. A run saved before apply-data archiving has no status to apply -- we say so and fall
    back to the file picker.
    """
    ext_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    history_dir = runner.load_settings(ext_root).get("history_dir")
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
            run = labels[choice]
            data = runhist.load_run_status(history_dir, run.get("id"))
            if data is None:
                forms.alert("'%s' was saved before apply-data archiving, so it can't be applied. "
                            "Re-run it, or pick a status.json file." % run.get("name", "run"),
                            title="SteelReuse")
                # fall through to the file picker
            else:
                return data

    json_path = forms.pick_file(file_ext="json", title="Pick the SteelReuse apply-matches JSON")
    if not json_path:
        return None
    with open(json_path) as fh:
        return json.load(fh)


def main():
    data = _pick_status_data()
    if data is None:
        return

    side = forms.CommandSwitchWindow.show(
        ["donor", "demand"],
        message="Is THIS open model the DONOR (reclaimed supply) or the DEMAND (new design)?",
    )
    if not side:
        return
    statuses = data.get(side, {})
    if not statuses:
        forms.alert("No '%s' entries in this JSON." % side)
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


if __name__ == "__main__":
    main()
