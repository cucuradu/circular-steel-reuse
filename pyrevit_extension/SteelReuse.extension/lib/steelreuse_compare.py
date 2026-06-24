# -*- coding: utf-8 -*-
"""SteelReuse Compare Runs window: pick two saved match runs and see -- interactively -- what changed.

A pyRevit ``forms.WPFWindow`` (default IronPython 3 engine -- stdlib + .NET only, no f-strings). Pure
view over the headless ``steelreuse_runs`` (saved-run history) and ``steelreuse_panel_model``
(``kpi_table`` and ``diff``). No engine runs here.

The point of the upgrade over the old text dump: explicit **A (baseline)** and **B (current)**
pickers remove the "which is which" guesswork, the KPI deltas and per-slot changes render in real
grids, and every changed slot is clickable -- Zoom selects the demand member in the active model,
Trace jumps to its donor in the other extracted model (shared :mod:`steelreuse_revit_events`).
"""

import os

import steelreuse_panel_model as panelmodel
import steelreuse_revit_events as revit_events
import steelreuse_runner as runner
import steelreuse_runs as runhist
from pyrevit import forms

_DIR = os.path.dirname(__file__)


class _KpiRow:
    """One KPI row of the comparison grid (plain attributes so WPF binds them directly)."""

    __slots__ = ("label", "a", "b", "delta")

    def __init__(self, label, a, b, delta):
        self.label = label
        self.a = a
        self.b = b
        self.delta = delta


class _DiffRow:
    """One per-slot change (plain attributes for WPF; the ids drive Zoom/Trace)."""

    __slots__ = ("slot_id", "change", "demand_id", "frm", "to", "detail",
                 "donor_baseline", "donor_current")

    def __init__(self, change):
        self.slot_id = change.get("slot_id", "")
        self.change = change.get("change", "")
        self.demand_id = change.get("demand_id") or ""
        self.donor_baseline = change.get("donor_baseline")
        self.donor_current = change.get("donor_current")
        self.frm = self.donor_baseline if self.donor_baseline is not None else "-"
        self.to = self.donor_current if self.donor_current is not None else "-"
        self.detail = change.get("detail", "")


class CompareWindow(forms.WPFWindow):
    """Lists the saved runs and compares the two selected (A baseline vs B current)."""

    def __init__(self, ext_root):
        forms.WPFWindow.__init__(self, os.path.join(_DIR, "steelreuse_compare.xaml"))
        self._ext_root = ext_root
        self._settings = runner.load_settings(ext_root)
        self._history = self._settings.get("history_dir")
        self._runs = []
        self._all_diff = []   # the full _DiffRow list, before the change-type filter
        self._loading = False

        self.compare_button.Click += self._on_compare
        self.refresh_button.Click += self._on_refresh
        self.holder_button.Click += self._on_change_holder
        self.delete_a_button.Click += self._on_delete_a
        self.delete_b_button.Click += self._on_delete_b
        self.change_filter.SelectionChanged += self._apply_change_filter

        self._zoom_handler = revit_events.ZoomHandler()
        self._zoom_event = revit_events.make_event(self._zoom_handler)
        self._trace_handler = revit_events.TraceHandler()
        self._trace_event = revit_events.make_event(self._trace_handler)
        self.zoom_button.Click += self._on_zoom
        self.trace_button.Click += self._on_trace
        self.diff_grid.MouseDoubleClick += self._on_zoom

        self._reload()

    # -- run list ---------------------------------------------------------------------------------
    def _label(self, run):
        return "%s   |   %s   |   %s" % (run.get("name", "run"),
                                         run.get("params_label", ""), run.get("timestamp", ""))

    def _reload(self):
        self._runs = runhist.load_runs(self._history) if self._history else []
        labels = [self._label(r) for r in self._runs]
        self._loading = True
        self.run_a_combo.ItemsSource = labels
        self.run_b_combo.ItemsSource = list(labels)  # a separate list instance for the second combo
        self._loading = False
        if self._runs:
            self.run_a_combo.SelectedIndex = 1 if len(self._runs) > 1 else 0  # older run as baseline
            self.run_b_combo.SelectedIndex = 0                                # newest as current
            self.legend_text.Text = "Pick A and B, then Compare."
        else:
            self.legend_text.Text = ("No saved runs yet -- run matches in Run Match first (each is "
                                     "auto-saved), or use Change holder...")

    def _on_refresh(self, sender, args):
        self._reload()

    def _on_change_holder(self, sender, args):
        folder = forms.pick_folder(title="Pick a steelreuse_runs holder folder")
        if not folder:
            return
        self._history = folder
        self._settings["history_dir"] = folder
        runner.save_settings(self._ext_root, self._settings)
        self._reload()

    def _run_at(self, combo):
        idx = combo.SelectedIndex
        if 0 <= idx < len(self._runs):
            return self._runs[idx]
        return None

    def _on_delete_a(self, sender, args):
        self._delete(self._run_at(self.run_a_combo))

    def _on_delete_b(self, sender, args):
        self._delete(self._run_at(self.run_b_combo))

    def _delete(self, run):
        if not run:
            return
        if not forms.alert("Delete saved run '%s'?" % run.get("name", run["id"]),
                           yes=True, no=True, title="SteelReuse"):
            return
        runhist.delete_run(self._history, run["id"])
        self._reload()

    # -- compare ----------------------------------------------------------------------------------
    def _on_compare(self, sender, args):
        run_a = self._run_at(self.run_a_combo)
        run_b = self._run_at(self.run_b_combo)
        if not run_a or not run_b:
            forms.alert("Pick a run for both A and B.", title="SteelReuse")
            return
        data_a = runhist.load_run_data(self._history, run_a["id"])
        data_b = runhist.load_run_data(self._history, run_b["id"])
        if data_a is None or data_b is None:
            forms.alert("Could not load the selected runs from the holder.", title="SteelReuse")
            return

        self.legend_text.Text = ("A = %s    ->    B = %s   (change column is B - A)"
                                 % (run_a.get("name", "A"), run_b.get("name", "B")))

        table = panelmodel.kpi_table([(run_a.get("name", "A"), data_a),
                                      (run_b.get("name", "B"), data_b)])
        kpi_rows = []
        for row in table["rows"]:
            a_val, b_val = row["values"][0], row["values"][1]
            kpi_rows.append(_KpiRow(row["label"], a_val, b_val, _signed_delta(a_val, b_val)))
        self.kpi_grid.ItemsSource = kpi_rows

        d = panelmodel.diff(data_a, data_b)
        self._all_diff = [_DiffRow(c) for c in d["slots"]]
        self._apply_change_filter(None, None)

    def _apply_change_filter(self, sender, args):
        kind = (self.change_filter.SelectedItem.Content
                if self.change_filter.SelectedItem else "all")
        if kind and kind != "all":
            rows = [r for r in self._all_diff if r.change == kind]
        else:
            rows = list(self._all_diff)
        self.diff_grid.ItemsSource = None
        self.diff_grid.ItemsSource = rows

    # -- drill-down -------------------------------------------------------------------------------
    def _selected_ids(self):
        row = self.diff_grid.SelectedItem
        if row is None:
            forms.alert("Select a changed slot first (or double-click it).", title="SteelReuse")
            return []
        ids = [row.demand_id, row.donor_baseline, row.donor_current]
        return [i for i in ids if i]

    def _on_zoom(self, sender, args):
        ids = self._selected_ids()
        if not ids:
            return
        self._zoom_handler.ids = ids
        self._zoom_event.Raise()

    def _on_trace(self, sender, args):
        ids = self._selected_ids()
        if not ids:
            return
        self._trace_handler.ids = ids
        self._trace_event.Raise()


def _signed_delta(a, b):
    """``b - a`` for the change column, rounded for floats; '' when either side is non-numeric."""
    if isinstance(a, bool) or isinstance(b, bool):
        return ""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        delta = b - a
        if isinstance(a, float) or isinstance(b, float):
            delta = round(delta, 1)
        prefix = "+" if delta > 0 else ""
        return prefix + str(delta)
    return ""
