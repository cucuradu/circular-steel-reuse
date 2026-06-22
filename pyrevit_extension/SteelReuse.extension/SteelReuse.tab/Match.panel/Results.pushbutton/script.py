# -*- coding: utf-8 -*-
"""Show the filterable results of the last match in pyRevit's output window.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why).
Stdlib only, no f-strings, %-formatting.

Reads the ``results.json`` written by **Run Match** (remembered in the runner config), or a file the
user picks, and renders it with :mod:`steelreuse_results_view`. No engine run happens here -- this is
pure review/filtering of an existing match. Element selection/zoom is the native panel's job later.
"""

import json
import os

import steelreuse_results_view as resultsview  # noqa: E402 -- extension lib/ is on the path
import steelreuse_runner as runner  # noqa: E402 -- extension lib/ is on the path
from pyrevit import forms, script

output = script.get_output()

# .../Results.pushbutton -> Match.panel -> SteelReuse.tab -> SteelReuse.extension
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _print_summary_md(data):
    """A plain-markdown KPI summary, so the output window is never blank even if the rich HTML view
    fails to render in this pyRevit/Revit build (the HTML table relies on the output WebView)."""
    k = data.get("kpis", {})
    co2 = k.get("co2_saved_kg")
    output.print_md("## SteelReuse match results")
    output.print_md("**%s / %s** slots reused  |  **%s** kg CO2e saved  |  %s distinct sections  |  %s"
                    % (k.get("reused", "?"), k.get("slots", "?"),
                       "?" if co2 is None else ("%.0f" % co2), k.get("distinct_sections", "?"),
                       "proven optimal" if k.get("proven_optimal") else "heuristic (not proven)"))
    unfilled = data.get("unfilled", [])
    quar = data.get("quarantined_donors", [])
    if unfilled:
        output.print_md("- **%d** demand slot(s) need new steel" % len(unfilled))
    if quar:
        output.print_md("- **%d** donor(s) quarantined (excluded from matching)" % len(quar))


def _locate_results():
    """The last run's results.json if it still exists, else ask the user to pick one."""
    last = runner.load_settings(_EXT_ROOT).get("last_results")
    if last and os.path.isfile(last):
        return last
    return forms.pick_file(file_ext="json", title="Select a SteelReuse results.json")


def main():
    path = _locate_results()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as ex:  # noqa: BLE001
        forms.alert("Could not read results:\n\n%s" % ex, title="SteelReuse")
        return
    if data.get("schema_version") not in (1, 2):
        output.print_md("> Note: this results file has an unexpected schema version (%s); "
                        "the view may be incomplete." % data.get("schema_version"))
    _print_summary_md(data)   # plain-text fallback first: always visible even if the HTML view below
    output.print_html(resultsview.render_results_html(data))  # does not render in this pyRevit build


if __name__ == "__main__":
    main()
