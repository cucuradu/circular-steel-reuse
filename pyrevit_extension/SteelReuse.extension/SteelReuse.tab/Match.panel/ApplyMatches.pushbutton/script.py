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

import steelreuse_apply as apply_mod  # noqa: E402 -- extension lib/ is on the path
from pyrevit import forms, revit, script

output = script.get_output()
doc = revit.doc

MAX_LINKS = 25  # cap the clickable list per status so huge models stay readable


def main():
    json_path = forms.pick_file(file_ext="json", title="Pick the SteelReuse apply-matches JSON")
    if not json_path:
        return
    with open(json_path) as fh:
        data = json.load(fh)

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
