# -*- coding: utf-8 -*-
"""SteelReuse Results window: review ANY saved match run interactively and trace its members in Revit.

A pyRevit ``forms.WPFWindow`` (default IronPython 3 engine -- stdlib + .NET only, no f-strings). Unlike
the Run Match panel (which shows the run it just produced), this window is pure review: it lists the
saved runs from the history holder, loads whichever the engineer picks, binds its assignments to the
grid, and -- the whole point -- lets each row be selected/zoomed in the model or traced to its partner
in the other extracted model. No engine runs here.

All the parsing/filtering lives in the headless :mod:`steelreuse_panel_model`; the Revit document
actions go through the shared :mod:`steelreuse_revit_events` ExternalEvent handlers (also used by Run
Match), so this file is just the view + wiring.
"""

import csv
import json
import os

import steelreuse_panel_model as panelmodel
import steelreuse_results_view as resultsview  # render the printable HTML report on demand
import steelreuse_revit_events as revit_events  # shared select/zoom/trace/apply ExternalEvent handlers
import steelreuse_runner as runner
import steelreuse_runs as runhist
from pyrevit import forms

_DIR = os.path.dirname(__file__)


class ResultsWindow(forms.WPFWindow):
    """Lists the saved runs (from the configured history holder) and reviews the selected one."""

    def __init__(self, ext_root):
        forms.WPFWindow.__init__(self, os.path.join(_DIR, "steelreuse_results_window.xaml"))
        self._ext_root = ext_root
        self._settings = runner.load_settings(ext_root)
        self._history = self._settings.get("history_dir")
        self._runs = []          # saved-run manifest entries, newest first
        self._data = None        # the raw results.json dict currently loaded
        self._view = None        # its parsed ResultsView
        self._rows = []          # currently-displayed (filtered) rows, for CSV export
        self._run_id = None      # the loaded run's id (None when an arbitrary file was opened)
        self._source_path = None  # the file/folder a loaded run came from, for Open folder/report
        self._loading = False    # guard so populating the combo does not trigger a load per item

        self.run_combo.SelectionChanged += self._on_pick_run
        self.refresh_button.Click += self._on_refresh
        self.holder_button.Click += self._on_change_holder
        self.openfile_button.Click += self._on_open_file
        self.status_filter.SelectionChanged += self._apply_filters
        self.section_filter.TextChanged += self._apply_filters
        self.minutil_filter.TextChanged += self._apply_filters

        # Document actions from this modeless window go through ExternalEvents (shared handlers).
        self._zoom_handler = revit_events.ZoomHandler()
        self._zoom_event = revit_events.make_event(self._zoom_handler)
        self._trace_handler = revit_events.TraceHandler()
        self._trace_event = revit_events.make_event(self._trace_handler)
        self._apply_handler = revit_events.ApplyHandler()
        self._apply_event = revit_events.make_event(self._apply_handler)
        self.zoom_button.Click += self._on_zoom
        self.trace_button.Click += self._on_trace
        self.grid.MouseDoubleClick += self._on_zoom
        self.apply_button.Click += self._on_apply
        self.open_report_button.Click += self._open_report
        self.open_evidence_button.Click += self._open_evidence
        self.open_folder_button.Click += self._open_folder
        self.export_button.Click += self._export_csv

        self._reload_runs()

    # -- run list ---------------------------------------------------------------------------------
    def _label(self, run):
        return "%s   |   %s   |   %s" % (run.get("name", "run"),
                                         run.get("params_label", ""), run.get("timestamp", ""))

    def _reload_runs(self):
        """Repopulate the run picker from the history holder and load the newest run."""
        self._runs = runhist.load_runs(self._history) if self._history else []
        self._loading = True
        self.run_combo.ItemsSource = [self._label(r) for r in self._runs]
        self._loading = False
        if self._runs:
            self.run_combo.SelectedIndex = 0  # newest first -> loads via _on_pick_run
        else:
            self.kpi_text.Text = ("No saved runs in the holder. Run a match in Run Match (each is "
                                  "auto-saved), or use Open file... / Change holder...")

    def _on_refresh(self, sender, args):
        self._reload_runs()

    def _on_change_holder(self, sender, args):
        folder = forms.pick_folder(title="Pick a steelreuse_runs holder folder")
        if not folder:
            return
        self._history = folder
        self._settings["history_dir"] = folder
        runner.save_settings(self._ext_root, self._settings)
        self._reload_runs()

    def _on_open_file(self, sender, args):
        """Review an arbitrary results.json that is not in the holder (no run id -> no apply)."""
        path = forms.pick_file(file_ext="json", title="Select a SteelReuse results.json")
        if not path:
            return
        data = self._read_json(path)
        if data is None:
            return
        self._run_id = None
        self._source_path = path
        self._bind(data)

    # -- load + bind ------------------------------------------------------------------------------
    def _read_json(self, path):
        try:
            with open(path) as handle:
                return json.load(handle)
        except Exception as ex:  # noqa: BLE001
            forms.alert("Could not read results:\n\n" + str(ex), title="SteelReuse")
            return None

    def _on_pick_run(self, sender, args):
        if self._loading:
            return
        idx = self.run_combo.SelectedIndex
        if idx < 0 or idx >= len(self._runs):
            return
        run = self._runs[idx]
        data = runhist.load_run_data(self._history, run["id"])
        if data is None:
            forms.alert("Could not load that run from the holder.", title="SteelReuse")
            return
        self._run_id = run["id"]
        self._source_path = self._history
        self._bind(data)

    def _bind(self, data):
        self._data = data
        self._view = panelmodel.parse(data)
        k = self._view.kpis
        warn = "" if self._view.schema_ok else "[unexpected schema] "
        line = (
            "%s%s / %s slots reused    |    %s kg CO2e saved    |    %s kg reused    |    %s"
            % (warn, k.get("reused", "?"), k.get("slots", "?"), k.get("co2_saved_kg", "?"),
               k.get("mass_reused_kg", "?"),
               "proven optimal" if k.get("proven_optimal") else "heuristic (not proven)"))
        # Roadmap §1.2: name the rule-data version + the donor-provenance coverage on the header, so a
        # reviewer sees "which rules, and that nothing was dropped" without opening the report.
        rules = self._view.rules or {}
        if rules.get("ruleset_version"):
            line += "    |    rules v%s" % rules.get("ruleset_version")
        ms = (self._view.mismatch or {}).get("summary") or {}
        if ms:
            line += ("    |    donors: %s mapped / %s fuzzy / %s unknown / %s quarantined"
                     % (ms.get("mapped", 0), ms.get("fuzzy", 0),
                        ms.get("unknown", 0), ms.get("quarantined", 0)))
        self.kpi_text.Text = line
        self._bind_provenance(rules, ms)
        self._apply_filters(None, None)

    def _bind_provenance(self, rules, summary):
        """Fill the native 'Donor provenance' tab: the rule-data versions + the per-donor mismatch
        grid (Roadmap §1.2). Older runs without the block show a hint and an empty grid."""
        self.mismatch_grid.ItemsSource = self._view.mismatch_rows
        if summary:
            cover = "100%" if summary.get("accounts_for_all") else "INCOMPLETE"
            self.provenance_summary_text.Text = (
                "Rule data: ruleset v%s.    Donor provenance: %s of %s donor row(s) accounted for "
                "(%s) -- %s mapped / %s fuzzy / %s unknown / %s quarantined. "
                "Every donor is classified with a reason below; the signable evidence package is one "
                "click away (Open evidence)."
                % (rules.get("ruleset_version", "?"), summary.get("n_donor_rows", "?"),
                   summary.get("n_donor_rows", "?"), cover, summary.get("mapped", 0),
                   summary.get("fuzzy", 0), summary.get("unknown", 0), summary.get("quarantined", 0)))
        else:
            self.provenance_summary_text.Text = (
                "This run has no provenance log (it predates the feature). Re-run it in Run Match to "
                "populate the donor mismatch log + rule versions.")

    # -- display filters (never re-run a match) ---------------------------------------------------
    def _apply_filters(self, sender, args):
        if not self._view:
            return
        status = (self.status_filter.SelectedItem.Content
                  if self.status_filter.SelectedItem else "all")
        try:
            min_util = float(self.minutil_filter.Text) if self.minutil_filter.Text.strip() else 0.0
        except ValueError:
            min_util = 0.0
        self._rows = panelmodel.filter_rows(
            self._view.rows, status=status, section=self.section_filter.Text, min_util=min_util)
        self.grid.ItemsSource = None
        self.grid.ItemsSource = self._rows

    # -- drill-down (select/zoom/trace via ExternalEvent) -----------------------------------------
    def _selected_ids(self):
        """The selected row's [demand_id, donor_id] (omitting blanks), or [] when nothing is picked."""
        row = self.grid.SelectedItem
        if row is None:
            forms.alert("Select an assignment row first (or double-click it).", title="SteelReuse")
            return []
        return [getattr(row, attr) for attr in ("demand_id", "donor_id") if getattr(row, attr, "")]

    def _on_zoom(self, sender, args):
        ids = self._selected_ids()
        if not ids:
            return
        self._zoom_handler.ids = ids
        self._zoom_event.Raise()  # Revit selects+zooms in the ACTIVE model at the next valid context

    def _on_trace(self, sender, args):
        ids = self._selected_ids()
        if not ids:
            return
        self._trace_handler.ids = ids
        self._trace_event.Raise()  # jumps to the partner element in the OTHER open model

    def _on_apply(self, sender, args):
        if self._run_id is None:
            forms.alert("Open a saved run (from the holder) to apply it -- an arbitrary file has no "
                        "archived apply-status.", title="SteelReuse")
            return
        statuses_all = runhist.load_run_status(self._history, self._run_id)
        if statuses_all is None:
            forms.alert("This run has no archived apply-status (it predates status archiving).\n"
                        "Re-run it in Run Match to make it applicable.", title="SteelReuse")
            return
        side = forms.CommandSwitchWindow.show(
            ["demand", "donor"],
            message="Is the OPEN model the DEMAND (new design) or the DONOR (supply)?")
        if not side:
            return
        statuses = statuses_all.get(side, {})
        if not statuses:
            forms.alert("No '%s' entries in this run's status." % side, title="SteelReuse")
            return
        self._apply_handler.statuses = statuses
        self._apply_handler.side = side
        self._apply_event.Raise()

    # -- report actions ---------------------------------------------------------------------------
    def _open_report(self, sender, args):
        """Render the loaded run to a standalone HTML report and open it in the browser.

        Built fresh from the loaded data (not the original out_dir report.html, which may be gone),
        so a saved run is always printable. Uses the shared writer that the Review buttons use.
        """
        if not self._data:
            forms.alert("Load a run first.", title="SteelReuse")
            return
        out_dir = self._history if (self._history and os.path.isdir(self._history)) \
            else os.path.dirname(self._source_path or "")
        name = ("run_" + self._run_id) if self._run_id else "results"
        out_path = os.path.join(out_dir or _DIR, name + "_view.html")
        try:
            html = resultsview.render_results_html(self._data)
            runner.open_html_report(out_path, "SteelReuse match results", html)
        except Exception as ex:  # noqa: BLE001
            forms.alert("Could not open the report:\n" + str(ex), title="SteelReuse")

    def _open_evidence(self, sender, args):
        """Open the signable per-run evidence package (Roadmap §1.1) directly -- no folder hunting.

        Prefers the copy archived with the saved run (``evidence_<id>.json`` in the holder); falls back
        to the path stamped in results.json or a sibling ``evidence.json`` for an arbitrary opened file.
        """
        path = None
        if self._run_id and self._history:
            path = runhist.run_artifact_path(self._history, self._run_id, "evidence_file")
        if not path:
            stamped = (self._view.paths or {}).get("evidence") if self._view else None
            if stamped and os.path.isfile(stamped):
                path = stamped
            elif self._source_path:
                sibling = os.path.join(os.path.dirname(self._source_path), "evidence.json")
                if os.path.isfile(sibling):
                    path = sibling
        if path and os.path.isfile(path):
            os.startfile(path)
        else:
            forms.alert("No evidence package is archived for this run.\n\nIt is generated for runs done "
                        "after this feature was added -- re-run the match in Run Match to produce the "
                        "signable evidence.json (and the donor mismatch log).", title="SteelReuse")

    def _open_folder(self, sender, args):
        target = self._history if (self._history and os.path.isdir(self._history)) \
            else os.path.dirname(self._source_path or "")
        if target and os.path.isdir(target):
            os.startfile(target)
        else:
            forms.alert("No holder folder to open yet.", title="SteelReuse")

    def _export_csv(self, sender, args):
        if not self._rows:
            forms.alert("Nothing to export -- load a run first.", title="SteelReuse")
            return
        target = forms.save_file(file_ext="csv")
        if not target:
            return
        with open(target, "w") as handle:
            writer = csv.writer(handle)
            writer.writerow(["slot", "demand_id", "demand_section", "donor_id", "donor_section",
                             "utilization", "status", "governing", "co2_saved_kg"])
            for r in self._rows:
                # Format numerics as strings so IronPython float repr noise stays out of the CSV.
                writer.writerow([r.slot_id, r.demand_id, r.demand_section, r.donor_id,
                                 r.donor_section, "%.3f" % r.utilization, r.status, r.governing,
                                 "%.1f" % r.co2_saved_kg])
        forms.alert("Exported %s rows to:\n%s" % (len(self._rows), target), title="SteelReuse")
