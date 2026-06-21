# -*- coding: utf-8 -*-
"""Show the PDA QA report for the last extraction review in pyRevit's output window."""

import json
import os

import steelreuse_review_view as reviewview  # noqa: E402
import steelreuse_runner as runner  # noqa: E402
from pyrevit import forms, script

output = script.get_output()
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def main():
    saved = runner.load_settings(_EXT_ROOT).get("interpreter")
    interp = runner.discover_interpreter(saved, _EXT_ROOT)
    donor = runner.load_settings(_EXT_ROOT).get("last_donor")
    if not (donor and os.path.isfile(donor)):
        donor = forms.pick_file(file_ext="json", title="Select the extracted donor.json")
    if not interp or not donor:
        return
    out_dir = os.path.join(_EXT_ROOT, "steelreuse_reports")
    res = runner.run_review(interp, {"donor": donor}, out_dir)
    if not res["ok"]:
        forms.alert("Review failed:\n\n%s" % (res["stderr"] or res["stdout"]), title="SteelReuse")
        return
    with open(res["paths"]["review_json"], encoding="utf-8") as handle:
        review = json.load(handle)
    output.print_html(reviewview.render_pda_report(review))


if __name__ == "__main__":
    main()
