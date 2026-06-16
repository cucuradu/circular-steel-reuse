# -*- coding: utf-8 -*-
"""SteelReuse Compare Runs window: pick saved match runs and compare them.

A pyRevit ``forms.WPFWindow`` (default IronPython 3 engine -- stdlib + .NET only, no f-strings). Pure
view over the headless ``steelreuse_runs`` (the saved-run history) and ``steelreuse_panel_model``
(``kpi_table`` for N runs, ``diff`` for the per-slot detail of exactly two). No engine runs here.
"""

import os

import steelreuse_panel_model as panelmodel  # extension lib/ is on the engine path
import steelreuse_runner as runner
import steelreuse_runs as runhist
from pyrevit import forms

_DIR = os.path.dirname(__file__)


class CompareWindow(forms.WPFWindow):
    """Lists the saved runs (from the configured history dir) and compares the selected ones."""

    def __init__(self, ext_root):
        forms.WPFWindow.__init__(self, os.path.join(_DIR, "steelreuse_compare.xaml"))
        self._history = runner.load_settings(ext_root).get("history_dir")
        self._runs = []
        self.compare_button.Click += self._on_compare
        self.delete_button.Click += self._on_delete
        self.refresh_button.Click += self._on_refresh
        self._reload()

    # -- list ------------------------------------------------------------------------------------
    def _label(self, run):
        return "%s   |   %s   |   %s" % (run.get("name", "run"),
                                         run.get("params_label", ""), run.get("timestamp", ""))

    def _reload(self):
        self._runs = runhist.load_runs(self._history) if self._history else []
        self.runs_list.ItemsSource = None
        self.runs_list.ItemsSource = [self._label(r) for r in self._runs]
        if not self._runs:
            self.compare_output.Text = ("No saved runs yet -- run a match in the Run Match window "
                                        "first (each run is auto-saved under its Run name).")

    def _selected_runs(self):
        labels = [self._label(r) for r in self._runs]
        chosen = []
        for item in self.runs_list.SelectedItems:
            if item in labels:
                chosen.append(self._runs[labels.index(item)])
        return chosen

    # -- actions ---------------------------------------------------------------------------------
    def _on_refresh(self, sender, args):
        self._reload()

    def _on_delete(self, sender, args):
        for run in self._selected_runs():
            runhist.delete_run(self._history, run["id"])
        self._reload()

    def _on_compare(self, sender, args):
        chosen = self._selected_runs()
        if len(chosen) < 2:
            forms.alert("Select at least two runs to compare (Ctrl/Shift-click).", title="SteelReuse")
            return
        named = []
        for run in chosen:
            data = runhist.load_run_data(self._history, run["id"])
            if data is not None:
                named.append((run["name"], data))
        if len(named) < 2:
            forms.alert("Could not load the selected runs.", title="SteelReuse")
            return

        table = panelmodel.kpi_table(named)
        fmt = "%-20s" + ("%16s" * len(table["columns"]))
        lines = [fmt % tuple(["KPI"] + list(table["columns"])), ""]
        for row in table["rows"]:
            lines.append(fmt % tuple([row["label"]] + [str(v) for v in row["values"]]))

        # Per-slot detail only makes sense for a strict pair.
        if len(named) == 2:
            d = panelmodel.diff(named[0][1], named[1][1])
            lines += ["", "Per-slot changes  %s -> %s  (%s):"
                      % (named[0][0], named[1][0], len(d["slots"])), ""]
            for c in d["slots"][:200]:
                lines.append("  %-16s %-7s %s" % (c["slot_id"], c["change"].upper(), c["detail"]))
            if len(d["slots"]) > 200:
                lines.append("  ... +%s more" % (len(d["slots"]) - 200))
        self.compare_output.Text = "\n".join(lines)
