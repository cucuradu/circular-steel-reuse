# -*- coding: utf-8 -*-
"""Run a full steel-reuse match from inside Revit -- no command line.

Runs on the default IronPython 3 engine (no ``#! python3`` -- see Extract.pushbutton for why).
Stdlib only, no f-strings, %-formatting.

The heavy matching engine never runs in Revit (CLAUDE.md hard rule 2). This button collects the run
options, then hands them to :mod:`steelreuse_runner` (in the extension ``lib/``), which shells out to
the signed CPython venv via ``python -m steelreuse.cli``. The three artifacts land in a
``steelreuse_reports`` folder beside the new-design model:

  * ``status.json``  -> feed to **Apply Matches** to colour this model;
  * ``report.html``  -> the full HTML report;
  * ``results.json`` -> the results view (the versioned contract; shown here and by the Results button).

The run is **synchronous**: Revit is busy for the ~30 s it takes on a large model, then the results
print into this output window. (A background-thread version froze the output window; kept simple and
reliable for now -- a non-blocking run can come back once the basics are proven in Revit.)

The interpreter path is remembered (with the last donor/demand) in the extension's runner config, so
after the first setup a run is: Run Match -> confirm models -> pick objective -> wait.
"""

import json
import os

import steelreuse_results_view as resultsview  # noqa: E402 -- extension lib/ is on the path
import steelreuse_runner as runner  # noqa: E402 -- pyRevit puts the extension lib/ on the path
from pyrevit import forms, script

output = script.get_output()

# .../RunMatch.pushbutton -> Match.panel -> SteelReuse.tab -> SteelReuse.extension
_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _resolve_interpreter(settings):
    """The signed-venv python.exe to run the engine with: remembered, auto-detected, or typed in."""
    interp = runner.discover_interpreter(settings.get("interpreter"), _EXT_ROOT)
    if interp:
        return interp
    # A TEXT box, not a file dialog: pasting a path here cannot accidentally launch python.exe
    # (a file dialog runs an .exe whose full path you type in and Enter).
    typed = forms.ask_for_string(
        default="",
        prompt="Paste the full path to the SteelReuse python.exe (the signed-venv interpreter that "
               "runs 'python -m steelreuse.cli'). It is remembered for next time.",
        title="SteelReuse: locate Python")
    if not typed:
        return None
    return typed.strip().strip('"')


def _pick_models(settings):
    """Return (donor, demand) JSON paths -- reuse the last pair on confirmation, else pick fresh."""
    last_donor = settings.get("last_donor")
    last_demand = settings.get("last_demand")
    if last_donor and last_demand and os.path.isfile(last_donor) and os.path.isfile(last_demand):
        reuse = forms.alert("Reuse the last models?\n\nDonor:  %s\nDemand: %s"
                            % (os.path.basename(last_donor), os.path.basename(last_demand)),
                            title="SteelReuse: models", yes=True, no=True)
        if reuse:
            return last_donor, last_demand
    donor = forms.pick_file(file_ext="json", title="Donor (supply) JSON")
    if not donor:
        return None, None
    demand = forms.pick_file(file_ext="json", title="New-design (demand) JSON")
    if not demand:
        return None, None
    return donor, demand


def _collect_options(donor, demand):
    """Quick run form: objective (pick one) + a few toggles. Numeric knobs use CLI defaults for now."""
    no_cut = "Whole-member only (no cutting)"
    frame = "Global frame analysis"
    verify = "Verify match is optimal"
    result = forms.CommandSwitchWindow.show(
        ["co2", "members", "mass"],
        switches=[no_cut, frame, verify],
        message="Matching objective (pick one); toggle options below:")
    # With switches, pyRevit returns (selected_option, {switch: bool}); be defensive either way.
    if isinstance(result, tuple):
        objective, switches = result
    else:
        objective, switches = result, {}
    if not objective:
        return None
    return {
        "donor": donor,
        "demand": demand,
        "objective": objective,
        "cut": not switches.get(no_cut, False),   # cutting-stock is the default
        "frame_analysis": switches.get(frame, False),
        "verify_match": switches.get(verify, False),
    }


def _report_success(res):
    """Print the headline KPIs, then the filterable results table, in the output window."""
    try:
        with open(res["paths"]["results"], encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:  # noqa: BLE001
        data = {}
    kpis = data.get("kpis", {})
    output.print_md("## SteelReuse match complete")
    output.print_md("- **%s / %s** slots filled by reuse"
                    % (kpis.get("reused", "?"), kpis.get("slots", "?")))
    output.print_md("- **%.0f kg** CO2e saved" % (kpis.get("co2_saved_kg") or 0.0))
    output.print_md("- objective **%s** -- %s"
                    % (kpis.get("objective", "?"),
                       "proven optimal" if kpis.get("proven_optimal") else "heuristic (not proven)"))
    output.print_md("Apply the colours with **Apply Matches** on `%s`. Full HTML report: `%s`."
                    % (res["paths"]["status"], res["paths"]["report"]))
    # The inline table is a bonus; never let a render hiccup hide the KPIs + file paths above.
    if data:
        try:
            output.print_html(resultsview.render_results_html(data))
        except Exception as ex:  # noqa: BLE001
            output.print_md("_(could not render the table inline: %s -- open the HTML report above.)_" % ex)


def main():
    settings = runner.load_settings(_EXT_ROOT)

    interp = _resolve_interpreter(settings)
    if not interp:
        forms.alert("No Python selected -- cannot run the match.", title="SteelReuse")
        return
    # Remember the interpreter immediately, so a cancelled run still skips the locate-Python step.
    if settings.get("interpreter") != interp:
        settings["interpreter"] = interp
        runner.save_settings(_EXT_ROOT, settings)

    donor, demand = _pick_models(settings)
    if not donor or not demand:
        return

    opts = _collect_options(donor, demand)
    if opts is None:
        return

    out_dir = os.path.join(os.path.dirname(demand), "steelreuse_reports")
    output.print_md("### SteelReuse: running the match...")
    output.print_md("Engine: `%s`" % interp)
    output.print_md("This can take ~30 s on a large model -- Revit will be busy until it finishes.")

    try:
        res = runner.run_match(interp, opts, out_dir)
    except Exception as ex:  # noqa: BLE001
        forms.alert("Could not start the match:\n\n%s" % ex, title="SteelReuse")
        return

    settings["last_donor"] = donor
    settings["last_demand"] = demand
    settings["last_results"] = runner.output_paths(out_dir)["results"]
    runner.save_settings(_EXT_ROOT, settings)

    if not res["ok"]:
        output.print_md("### SteelReuse match failed (exit %s)" % res["returncode"])
        detail = (res["stderr"] or res["stdout"] or "").strip()
        output.print_md("```\n%s\n```" % detail[-3000:])
        return
    _report_success(res)


if __name__ == "__main__":
    main()
