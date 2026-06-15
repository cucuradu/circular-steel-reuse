# -*- coding: utf-8 -*-
"""SteelReuse match window: run the whole pipeline and review the results in one panel, no terminal.

A pyRevit ``forms.WPFWindow`` (default IronPython 3 engine -- stdlib + .NET only, no f-strings). The
heavy matching engine never runs in Revit (CLAUDE.md hard rule 2): the Run button hands the form
options to :mod:`steelreuse_runner`, which shells out to the signed CPython venv on a **background
thread** so Revit never freezes; on completion the results.json (schema v2) is parsed by the headless
:mod:`steelreuse_panel_model` and bound to the grid.

Increment 1 (this file): donor/demand + objective + cutting, background run, KPI header, assignments
grid with status/section/min-util filters, and open-report / open-folder / export-CSV. The Advanced
option tabs and the extra result tabs (portfolio/pareto/disposition/audit/warnings) layer on next.
"""

import csv
import json
import os
import threading

import steelreuse_panel_model as panelmodel  # extension lib/ is on the engine path
import steelreuse_runner as runner
from pyrevit import forms
from System import Action  # marshal worker-thread results back to the WPF UI thread

_DIR = os.path.dirname(__file__)


class SteelReusePanel(forms.WPFWindow):
    """The run+review window. One instance per open; remembers the last inputs via the runner config."""

    def __init__(self, ext_root):
        forms.WPFWindow.__init__(self, os.path.join(_DIR, "steelreuse_panel.xaml"))
        self._ext_root = ext_root
        self._settings = runner.load_settings(ext_root)
        self._view = None        # parsed ResultsView of the last run
        self._rows = []          # currently-displayed (filtered) rows, for CSV export

        self.donor_browse.Click += self._pick_donor
        self.demand_browse.Click += self._pick_demand
        self.run_button.Click += self._on_run
        self.status_filter.SelectionChanged += self._apply_filters
        self.section_filter.TextChanged += self._apply_filters
        self.minutil_filter.TextChanged += self._apply_filters
        self.open_report_button.Click += self._open_report
        self.open_folder_button.Click += self._open_folder
        self.export_button.Click += self._export_csv
        self._restore()

    # -- setup ------------------------------------------------------------------------------------
    def _restore(self):
        donor = self._settings.get("last_donor")
        demand = self._settings.get("last_demand")
        if donor:
            self.donor_box.Text = donor
        if demand:
            self.demand_box.Text = demand if isinstance(demand, str) else "; ".join(demand)
        self.objective_combo.SelectedIndex = 0
        self.status_filter.SelectedIndex = 0

    # -- form -> options --------------------------------------------------------------------------
    def collect_options(self):
        """Read the controls into the plain options dict :func:`runner.build_command` consumes."""
        demand = [p.strip() for p in self.demand_box.Text.split(";") if p.strip()]
        return {
            "donor": self.donor_box.Text.strip(),
            "demand": demand,
            "objective": self.objective_combo.SelectedItem.Content,
            "cut": not bool(self.no_cut_check.IsChecked),
        }

    def _pick_donor(self, sender, args):
        path = forms.pick_file(file_ext="json", title="Donor (supply) JSON")
        if path:
            self.donor_box.Text = path

    def _pick_demand(self, sender, args):
        picked = forms.pick_file(file_ext="json", title="New-design (demand) JSON", multi_file=True)
        if picked:
            self.demand_box.Text = "; ".join(picked) if isinstance(picked, list) else picked

    # -- run (background thread) ------------------------------------------------------------------
    def _on_run(self, sender, args):
        opts = self.collect_options()
        if not opts["donor"] or not opts["demand"]:
            forms.alert("Pick a donor and a demand JSON first.", title="SteelReuse")
            return

        interp = runner.discover_interpreter(self._settings.get("interpreter"), self._ext_root)
        if not interp:
            typed = forms.ask_for_string(
                default="", title="SteelReuse: locate Python",
                prompt="Paste the full path to the signed-venv python.exe that runs "
                       "'python -m steelreuse.cli'. Remembered for next time.")
            if not typed:
                return
            interp = typed.strip().strip('"')

        self._settings["interpreter"] = interp
        self._settings["last_donor"] = opts["donor"]
        self._settings["last_demand"] = opts["demand"]
        runner.save_settings(self._ext_root, self._settings)

        out_dir = os.path.join(os.path.dirname(opts["demand"][0]), "steelreuse_reports")
        self.run_button.IsEnabled = False
        self.progress_box.Text = "Running the match... Revit stays responsive; this can take ~30 s.\n"
        threading.Thread(target=self._worker, args=(interp, opts, out_dir)).start()

    def _worker(self, interp, opts, out_dir):
        """Runs off the UI thread: shell out to the engine, then marshal the outcome back to WPF."""
        try:
            res = runner.run_match(interp, opts, out_dir)
        except Exception as ex:  # noqa: BLE001 -- surface any launch failure in the panel
            # Bind the message now: the lambda runs on the UI thread, after ``ex`` would be cleared.
            message = "Could not start the match:\n" + str(ex)
            self._ui(lambda: self._failed(message))
            return
        if not res["ok"]:
            detail = (res["stderr"] or res["stdout"] or "").strip()
            self._ui(lambda: self._failed(
                "Match failed (exit %s):\n%s" % (res["returncode"], detail[-2000:])))
            return
        self._ui(lambda: self._loaded(res["paths"]["results"], res.get("stdout", "")))

    def _ui(self, fn):
        self.Dispatcher.Invoke(Action(fn))

    def _failed(self, message):
        self.run_button.IsEnabled = True
        self.progress_box.Text = message

    def _loaded(self, results_path, stdout):
        self.run_button.IsEnabled = True
        try:
            with open(results_path) as handle:
                data = json.load(handle)
        except Exception as ex:  # noqa: BLE001
            self._failed("Could not read results:\n" + str(ex))
            return
        self._view = panelmodel.parse(data)
        k = self._view.kpis
        self.kpi_text.Text = (
            "%s / %s slots reused    |    %s kg CO2e saved    |    %s kg reused    |    %s"
            % (k.get("reused", "?"), k.get("slots", "?"), k.get("co2_saved_kg", "?"),
               k.get("mass_reused_kg", "?"),
               "proven optimal" if k.get("proven_optimal") else "heuristic (not proven)"))
        tail = "\n".join((stdout or "").strip().splitlines()[-6:])
        warn = "" if self._view.schema_ok else "WARNING: unexpected results schema version.\n"
        self.progress_box.Text = warn + "Done.\n" + tail
        self._apply_filters(None, None)

    # -- display filters (never re-run the match) -------------------------------------------------
    def _apply_filters(self, sender, args):
        if not self._view:
            return
        status = self.status_filter.SelectedItem.Content if self.status_filter.SelectedItem else "all"
        try:
            min_util = float(self.minutil_filter.Text) if self.minutil_filter.Text.strip() else 0.0
        except ValueError:
            min_util = 0.0
        self._rows = panelmodel.filter_rows(
            self._view.rows, status=status, section=self.section_filter.Text, min_util=min_util)
        self.grid.ItemsSource = None
        self.grid.ItemsSource = self._rows

    # -- footer actions ---------------------------------------------------------------------------
    def _report_path(self):
        return self._view.paths.get("report") if self._view else None

    def _open_report(self, sender, args):
        path = self._report_path()
        if path and os.path.isfile(path):
            os.startfile(path)
        else:
            forms.alert("No report yet -- run a match first.", title="SteelReuse")

    def _open_folder(self, sender, args):
        path = self._report_path()
        if path:
            os.startfile(os.path.dirname(path))

    def _export_csv(self, sender, args):
        if not self._rows:
            forms.alert("Nothing to export -- run a match first.", title="SteelReuse")
            return
        target = forms.save_file(file_ext="csv")
        if not target:
            return
        with open(target, "w") as handle:
            writer = csv.writer(handle)
            writer.writerow(["slot", "demand_section", "donor", "donor_section", "utilization",
                             "status", "governing", "co2_saved_kg"])
            for r in self._rows:
                # Format the numerics as strings so IronPython float repr noise
                # (0.25900000000000001) stays out of the exported CSV.
                writer.writerow([r.slot_id, r.demand_section, r.donor_id, r.donor_section,
                                 "%.3f" % r.utilization, r.status, r.governing,
                                 "%.1f" % r.co2_saved_kg])
        forms.alert("Exported %s rows to:\n%s" % (len(self._rows), target), title="SteelReuse")
