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
import steelreuse_revit_events as revit_events  # shared select/zoom/apply ExternalEvent handlers
import steelreuse_runner as runner
import steelreuse_runs as runhist  # auto-save each run to the Compare Runs history
from pyrevit import forms
from System import Action  # marshal worker-thread results back to the WPF UI thread
from System.Windows import Visibility  # show/hide the optional result tabs

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
        self.pda_browse.Click += self._pick_pda
        self.template_button.Click += self._make_template
        self.run_button.Click += self._on_run
        self.status_filter.SelectionChanged += self._apply_filters
        self.section_filter.TextChanged += self._apply_filters
        self.minutil_filter.TextChanged += self._apply_filters
        self.open_report_button.Click += self._open_report
        self.open_folder_button.Click += self._open_folder
        self.export_button.Click += self._export_csv
        # Document actions (select/zoom) from this modeless window go through an ExternalEvent.
        self._zoom_handler = revit_events.ZoomHandler()
        self._zoom_event = revit_events.make_event(self._zoom_handler)
        self.zoom_button.Click += self._on_zoom
        self.grid.MouseDoubleClick += self._on_zoom
        self._apply_handler = revit_events.ApplyHandler()
        self._apply_event = revit_events.make_event(self._apply_handler)
        self.apply_button.Click += self._on_apply
        # Optional result tabs start hidden; a run reveals the ones whose data is present.
        for tab in (self.tab_unfilled, self.tab_portfolio, self.tab_pareto,
                    self.tab_disposition, self.tab_marginal, self.tab_audit, self.tab_warnings):
            tab.Visibility = Visibility.Collapsed
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
        self.counterfactual_combo.SelectedIndex = 0
        self.solver_combo.SelectedIndex = 0
        self.status_filter.SelectedIndex = 0

    # -- form -> options --------------------------------------------------------------------------
    def _num(self, box):
        """A text box's value as float, or None when blank/unparseable (so the CLI default stands)."""
        text = box.Text.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _int(self, box):
        """A text box's value as int, or None when blank/unparseable."""
        text = box.Text.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None

    def collect_options(self):
        """Read every control into the plain options dict :func:`runner.build_command` consumes.

        Blank numeric fields map to None -> the flag is omitted -> the CLI's own default applies, so
        an untouched form reproduces the canonical case-study run.
        """
        demand = [p.strip() for p in self.demand_box.Text.split(";") if p.strip()]
        return {
            "donor": self.donor_box.Text.strip(),
            "demand": demand,
            "objective": self.objective_combo.SelectedItem.Content,
            "cut": not bool(self.no_cut_check.IsChecked),
            # Policy
            "min_util": self._num(self.min_util_box),
            "max_distinct_sections": self._int(self.max_sections_box),
            "w_overspec": self._num(self.w_overspec_box),
            "reserve": self._num(self.reserve_box),
            "connections": bool(self.connections_check.IsChecked),
            "verify_match": bool(self.verify_check.IsChecked),
            "pareto": bool(self.pareto_check.IsChecked),
            # Carbon
            "counterfactual": self.counterfactual_combo.SelectedItem.Content,
            "disposition": bool(self.disposition_check.IsChecked),
            # Loads
            "national_annex": self.national_annex_combo.SelectedItem.Content,
            "occupancy": self.occupancy_combo.SelectedItem.Content,
            "roof_occupancy": self.roof_occupancy_combo.SelectedItem.Content,
            "load_reduction": not bool(self.no_load_reduction_check.IsChecked),
            "dead": self._num(self.dead_box),
            "live": self._num(self.live_box),
            "gamma_g": self._num(self.gamma_g_box),
            "gamma_q": self._num(self.gamma_q_box),
            "trib_width": self._num(self.trib_width_box),
            "col_trib_area": self._num(self.col_trib_box),
            "col_floors": self._num(self.col_floors_box),
            "col_ecc": self._num(self.col_ecc_box),
            "trib_from_geometry": bool(self.trib_geom_check.IsChecked),
            "all_demand": bool(self.all_demand_check.IsChecked),
            # Load cases
            "phi": self._num(self.phi_box),
            "construction": bool(self.construction_check.IsChecked),
            "construction_live": self._num(self.construction_live_box),
            "wind_uplift": self._num(self.wind_uplift_box),
            # Frame
            "frame_analysis": bool(self.frame_check.IsChecked),
            "solver": self.solver_combo.SelectedItem.Content,
            "pdelta": bool(self.pdelta_check.IsChecked),
            "wind": self._num(self.wind_box),
            "seismic": self._num(self.seismic_box),
            # Audit & checks
            "pda": self.pda_box.Text.strip() or None,
            "include_unverified": bool(self.include_unverified_check.IsChecked),
            "knockdown": self._num(self.knockdown_box),
            "moment_shape": bool(self.moment_shape_check.IsChecked),
        }

    def _pick_donor(self, sender, args):
        path = forms.pick_file(file_ext="json|csv|xlsx", title="Donor (supply) model or inventory")
        if path:
            self.donor_box.Text = path

    def _pick_demand(self, sender, args):
        picked = forms.pick_file(file_ext="json|csv|xlsx", title="New-design (demand) model or inventory",
                                 multi_file=True)
        if picked:
            self.demand_box.Text = "; ".join(picked) if isinstance(picked, list) else picked

    def _pick_pda(self, sender, args):
        path = forms.pick_file(file_ext="csv", title="Pre-demolition audit CSV")
        if path:
            self.pda_box.Text = path

    def _make_template(self, sender, args):
        """Write a blank donor-inventory template (xlsx/csv) via the engine, then offer to open it."""
        target = forms.save_file(file_ext="xlsx", default_name="donor_inventory_template")
        if not target:
            return
        interp = runner.discover_interpreter(self._settings.get("interpreter"), self._ext_root)
        if not interp:
            forms.alert("Locate the signed-venv python first (use Run Match once to set it).",
                        title="SteelReuse")
            return
        res = runner.run_inventory_template(interp, target)
        if res["ok"]:
            self._settings["interpreter"] = interp
            runner.save_settings(self._ext_root, self._settings)
            if forms.alert("Blank inventory template written to:\n%s\n\nFill one row per reclaimed "
                           "member, then Browse to it as the Donor model. Open it now?" % target,
                           title="SteelReuse", ok=False, yes=True, no=True):
                os.startfile(target)
        else:
            detail = (res.get("stdout") or res.get("stderr") or "").strip()
            forms.alert("Could not write the template:\n\n%s" % detail[-1500:], title="SteelReuse")

    # -- run (background thread) ------------------------------------------------------------------
    def _on_run(self, sender, args):
        opts = self.collect_options()
        if not opts["donor"] or not opts["demand"]:
            forms.alert("Pick a donor and a demand model first (.json, .csv or .xlsx).",
                        title="SteelReuse")
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
            detail = (res["stdout"] or res["stderr"] or "").strip()
            hint = runner.describe_returncode(res["returncode"])
            if hint:
                log = res["paths"].get("log")
                detail = hint + (("\n\nLog: " + log) if log else "") + (("\n\n" + detail) if detail else "")
            message = "Match failed (exit %s):\n%s" % (res["returncode"], detail[-2000:])
            self._ui(lambda m=message: self._failed(m))
            return
        self._ui(lambda: self._loaded(res["paths"]["results"], res.get("stdout", ""), opts))

    def _ui(self, fn):
        self.Dispatcher.Invoke(Action(fn))

    def _failed(self, message):
        self.run_button.IsEnabled = True
        self.progress_box.Text = message

    def _loaded(self, results_path, stdout, opts):
        self.run_button.IsEnabled = True
        try:
            with open(results_path) as handle:
                data = json.load(handle)
        except Exception as ex:  # noqa: BLE001
            self._failed("Could not read results:\n" + str(ex))
            return
        self._view = panelmodel.parse(data)
        k = self._view.kpis
        line = (
            "%s / %s slots reused    |    %s kg CO2e saved    |    %s kg reused    |    %s"
            % (k.get("reused", "?"), k.get("slots", "?"), k.get("co2_saved_kg", "?"),
               k.get("mass_reused_kg", "?"),
               "proven optimal" if k.get("proven_optimal") else "heuristic (not proven)"))
        # Roadmap §1.2: name the rule-data version + donor-provenance coverage on the header, so the
        # externalised-rules / mismatch-log work is visible right here (details on the Provenance tab).
        rules = self._view.rules or {}
        if rules.get("ruleset_version"):
            line += "    |    rules v%s" % rules.get("ruleset_version")
        ms = (self._view.mismatch or {}).get("summary") or {}
        if ms:
            line += ("    |    donors: %s mapped / %s fuzzy / %s unknown / %s quarantined"
                     % (ms.get("mapped", 0), ms.get("fuzzy", 0),
                        ms.get("unknown", 0), ms.get("quarantined", 0)))
        self.kpi_text.Text = line
        # Show the FULL engine log (scrollable), so the "Forces: frame analysis (solver) -- N nodes"
        # line and other run details are visible -- the tail alone hid which backend actually ran.
        warn = "" if self._view.schema_ok else "WARNING: unexpected results schema version.\n"
        self.progress_box.Text = warn + "Done.\n\n" + (stdout or "").strip()
        self.progress_box.ScrollToHome()
        self._render_tabs()
        self._apply_filters(None, None)
        try:
            self._save_to_history(results_path, opts)
        except Exception as ex:  # noqa: BLE001 -- a history-save failure must not break the run
            self.progress_box.Text += "\n\n(Note: could not save this run to history: %s)" % ex

    # -- result tabs (rendered as monospace tables; hidden when their block is absent) -------------
    def _render_tabs(self):
        v = self._view
        diag = v.diagnosis or {}
        binding = diag.get("binding_constraint")
        if binding and binding != "none":
            self.diagnosis_text.Text = ("Binding constraint: %s.  %s"
                                        % (binding, diag.get("lever", "")))
        else:
            self.diagnosis_text.Text = "Every demand slot that could be filled was filled."
        unfilled = v.unfilled
        lines = ["%s unfilled slot(s) need new steel:" % len(unfilled), ""]
        for r in unfilled:
            detail = r.get("reason_detail", "")
            lines.append("  %-16s %-12s %s"
                         % (r.get("slot_id", ""), r.get("demand_section", ""), detail))
        self.unfilled_text.Text = "\n".join(lines)
        self.tab_unfilled.Visibility = Visibility.Visible

        self.warnings_text.Text = self._fmt_warnings()
        self.tab_warnings.Visibility = Visibility.Visible

        self._opt_tab(self.tab_portfolio, v.has_portfolio, self.portfolio_text, self._fmt_portfolio)
        self._opt_tab(self.tab_pareto, v.has_pareto, self.pareto_text, self._fmt_pareto)
        self._opt_tab(self.tab_disposition, v.has_disposition,
                      self.disposition_text, self._fmt_disposition)
        self._opt_tab(self.tab_marginal, v.has_marginal_value,
                      self.marginal_text, self._fmt_marginal)
        self._opt_tab(self.tab_audit, v.has_audit, self.audit_text, self._fmt_audit)
        self._opt_tab(self.tab_provenance, v.has_mismatch,
                      self.provenance_text, self._fmt_provenance)

    def _opt_tab(self, tab, present, box, fmt):
        if present:
            box.Text = fmt()
            tab.Visibility = Visibility.Visible
        else:
            tab.Visibility = Visibility.Collapsed

    def _fmt_warnings(self):
        w = self._view.warnings
        out = [
            "LTB restraint-reliant beams : %s" % w.get("ltb_restraint_reliant", 0),
            "Imperfection-governed       : %s" % w.get("imperfection_governed", 0),
            "Donors cut to length        : %s   (remainder %s m)"
            % (w.get("cut_donors", 0), w.get("reusable_remainder_m", 0)),
            "Connection-review flags     : %s" % w.get("connection_review", 0),
            "Unidentified donor members  : %s" % w.get("unknown", 0),
        ]
        breakdown = w.get("unknown_breakdown", [])
        if breakdown:
            out += ["", "Unidentified breakdown (top 20):"]
            for b in breakdown[:20]:
                out.append("  %6s x %s" % (b.get("count", ""), b.get("name", "")))
        return "\n".join(out)

    def _fmt_pareto(self):
        out = ["%-10s %8s %14s %14s  %s"
               % ("objective", "reused", "CO2e (kg)", "mass (kg)", "optimal"), ""]
        for p in self._view.pareto:
            mark = "*" if p.get("selected") else " "
            out.append("%s%-9s %8s %14.1f %14.1f  %s"
                       % (mark, p.get("label") or p.get("objective", ""), p.get("n_reused", ""),
                          float(p.get("co2_saved_kg") or 0), float(p.get("mass_reused_kg") or 0),
                          "yes" if p.get("proven_optimal") else "no"))
        out += ["", "* = the objective this run's assignments follow."]
        return "\n".join(out)

    def _fmt_disposition(self):
        d = self._view.disposition
        t = d.get("totals", {})
        br = t.get("by_reason", {})
        out = [
            "Unused donors: %s    store %s | re-roll %s | recycle %s"
            % (t.get("n", ""), t.get("store", ""), t.get("reroll", ""), t.get("recycle", "")),
            "Potential credits: %.1f kg CO2e re-roll, %.1f kg CO2e recycle"
            % (float(t.get("reroll_credit_kg") or 0), float(t.get("recycle_credit_kg") or 0)),
        ]
        if br:
            out.append(
                "Why unused: too-short %s | too-weak %s | contention %s | uneconomic %s"
                % (br.get("too-short", 0), br.get("too-weak", 0),
                   br.get("contention", 0), br.get("uneconomic", 0)))
        out += [
            "",
            "%-12s %7s %6s %8s %8s" % ("section", "donors", "store", "re-roll", "recycle"), "",
        ]
        for r in d.get("by_section", []):
            out.append("%-12s %7s %6s %8s %8s"
                       % (r.get("section", ""), r.get("n", ""), r.get("store", ""),
                          r.get("reroll", ""), r.get("recycle", "")))
        return "\n".join(out)

    def _fmt_marginal(self):
        rows = sorted(self._view.marginal_value,
                      key=lambda r: -(r.get("marginal_co2_kg") or 0))
        out = [
            "What each reused donor is worth to the solution (re-solved without it):",
            "A small value = a close substitute exists; a large value = the result leans on it.",
            "",
            "%-10s %-12s %14s %12s %8s" % ("donor", "section", "marginal CO2e", "slots lost",
                                           "reshuf."), "",
        ]
        for r in rows:
            lost = ", ".join(r.get("slots_lost") or []) or "-"
            out.append("%-10s %-12s %14.1f %12s %8s"
                       % (r.get("supply_id", ""), r.get("section", ""),
                          float(r.get("marginal_co2_kg") or 0), lost,
                          r.get("reshuffled_slots", 0)))
        return "\n".join(out)

    def _fmt_portfolio(self):
        out = ["%-18s %6s %7s %14s %9s"
               % ("project", "slots", "reused", "CO2e (kg)", "unfilled"), ""]
        for p in self._view.portfolio:
            out.append("%-18s %6s %7s %14.1f %9s"
                       % (p.get("tag", ""), p.get("slot_count", ""), p.get("n_reused", ""),
                          float(p.get("co2_saved_kg") or 0), p.get("n_unmatched", "")))
        return "\n".join(out)

    def _fmt_audit(self):
        a = self._view.audit
        out = ["Audited %s | admitted %s | quarantined %s | avg knockdown %s"
               % (a.get("audited", ""), a.get("admitted", ""), a.get("quarantined", ""),
                  a.get("avg_knockdown", "")), "", "Verification basis:"]
        for v in a.get("verification", []):
            out.append("  %-14s %s" % (v.get("basis", ""), v.get("count", "")))
        out += ["", "Condition grade:"]
        for c in a.get("condition", []):
            out.append("  %-14s %s" % (c.get("grade", ""), c.get("count", "")))
        quarantined = a.get("quarantined_list", [])
        if quarantined:
            out += ["", "Quarantined (%s):" % len(quarantined)]
            for q in quarantined:
                out.append("  %-14s %s" % (q.get("id", ""), q.get("reason", "")))
        return "\n".join(out)

    def _fmt_provenance(self):
        """Rule-data versions + the donor mismatch log (Roadmap §1.2): every donor classified with a
        reason, so 'nothing was silently dropped' is visible here, not only in the evidence file."""
        v = self._view
        rules = v.rules or {}
        mismatch = v.mismatch or {}
        summary = mismatch.get("summary", {})
        rows = mismatch.get("rows", [])
        out = []
        if rules.get("ruleset_version"):
            tables = ", ".join("%s v%s" % (t.get("name", ""), t.get("version", ""))
                               for t in rules.get("tables", []))
            out += ["Rule data (externalised + versioned):",
                    "  ruleset v%s" % rules.get("ruleset_version"),
                    "  tables: %s" % tables,
                    "  carbon factors: v%s" % rules.get("carbon_factors_version", "?"), ""]
        cover = "100%" if summary.get("accounts_for_all") else "INCOMPLETE"
        out += ["Donor provenance -- %s of %s donor row(s) accounted for:"
                % (cover, summary.get("n_donor_rows", "?")),
                "  %s mapped | %s fuzzy | %s unknown | %s quarantined"
                % (summary.get("mapped", 0), summary.get("fuzzy", 0),
                   summary.get("unknown", 0), summary.get("quarantined", 0)), ""]
        out.append("%-14s %-11s %-9s %s" % ("donor id", "class", "outcome", "reason"))
        for r in rows:
            out.append("%-14s %-11s %-9s %s"
                       % (r.get("id", ""), r.get("classification", ""),
                          r.get("outcome", ""), r.get("reason", "")))
        out += ["", "The full signable evidence package (evidence.json) and this log (mismatch.csv)",
                "are in this run's folder -- use 'Open folder' below."]
        return "\n".join(out)

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

    # -- drill-down (select + zoom the element in the open model, via ExternalEvent) ---------------
    def _on_zoom(self, sender, args):
        row = self.grid.SelectedItem
        if row is None:
            forms.alert("Select an assignment row first (or double-click it).", title="SteelReuse")
            return
        ids = [getattr(row, attr) for attr in ("demand_id", "donor_id") if getattr(row, attr, "")]
        if not ids:
            return
        self._zoom_handler.ids = ids
        self._zoom_event.Raise()  # Revit runs the select+zoom when it next reaches a valid context

    def _on_apply(self, sender, args):
        status_path = self._view.paths.get("status") if self._view else None
        if not status_path or not os.path.isfile(status_path):
            forms.alert("Run a match first (no status.json to apply).", title="SteelReuse")
            return
        try:
            with open(status_path) as handle:
                data = json.load(handle)
        except Exception as ex:  # noqa: BLE001
            forms.alert("Could not read status.json:\n" + str(ex), title="SteelReuse")
            return
        side = forms.CommandSwitchWindow.show(
            ["demand", "donor"],
            message="Is the OPEN model the DEMAND (new design) or the DONOR (supply)?")
        if not side:
            return
        statuses = data.get(side, {})
        if not statuses:
            forms.alert("No '%s' entries in the status file." % side, title="SteelReuse")
            return
        self._apply_handler.statuses = statuses
        self._apply_handler.side = side
        self._apply_event.Raise()  # the colouring transaction runs in a valid Revit context

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

    # -- run history (auto-save each run for the separate Compare Runs tool) -----------------------
    def _params_label(self, opts):
        """A short human label of the run's options, shown in the Compare Runs history list."""
        parts = [opts.get("objective", "co2")]
        parts.append("no-cut" if not opts.get("cut", True) else "cut")
        if opts.get("frame_analysis"):
            parts.append("frame(" + str(opts.get("solver") or "pynite") + ")")
        if opts.get("pareto"):
            parts.append("pareto")
        if opts.get("construction"):
            parts.append("construction")
        if opts.get("counterfactual") and opts.get("counterfactual") != "none":
            parts.append(str(opts["counterfactual"]))
        if opts.get("min_util"):
            parts.append("min-util " + str(opts["min_util"]))
        return ", ".join(parts)

    def _save_to_history(self, results_path, opts):
        """Auto-save this run to the run history (steelreuse_runs/ beside the demand model).

        The run's apply-matches status.json (written next to results.json) is archived too, so the
        run can be re-applied to the model from the Apply Matches button later, not just the last run.
        """
        history_dir = os.path.join(
            os.path.dirname(os.path.dirname(results_path)), "steelreuse_runs")
        name = self.run_name_box.Text.strip()
        out_dir = os.path.dirname(results_path)
        status_path = os.path.join(out_dir, "status.json")
        # Archive the evidence package + mismatch log with the run too, so a saved run is reviewable
        # from the Results window (they are written to the live run folder by the engine).
        evidence_path = os.path.join(out_dir, "evidence.json")
        mismatch_path = os.path.join(out_dir, "mismatch.csv")
        runhist.record_run(history_dir, name, self._params_label(opts), results_path,
                           status_path=status_path, evidence_path=evidence_path,
                           mismatch_path=mismatch_path)
        self._settings["history_dir"] = history_dir
        runner.save_settings(self._ext_root, self._settings)
