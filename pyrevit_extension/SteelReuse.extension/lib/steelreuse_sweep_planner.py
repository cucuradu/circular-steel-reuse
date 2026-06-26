# -*- coding: utf-8 -*-
"""SteelReuse Scenario Sweep planner: lock the fixed problem, pick a few dials to vary, run every
combination at once, then open the ranked trade-off board.

A pyRevit ``forms.WPFWindow`` (default IronPython 3 engine -- stdlib + .NET only, no f-strings). All
the non-UI logic -- grid expansion, lean-ifying each point, running the bounded process pool, and the
Pareto front -- lives in the unit-tested :mod:`steelreuse_sweep`. This file is the thin Revit glue:
read the controls into a fixed base + varied axes, run ``sweep.run_grid`` on a background thread so
Revit stays responsive, then hand the collected + front-marked records to the board window.
"""

import os
import threading
import time

import steelreuse_buttons as buttons
import steelreuse_runner as runner
import steelreuse_sweep as sweep
import steelreuse_sweep_board as board
from pyrevit import forms
from System import Action

_DIR = os.path.dirname(__file__)

# Above this many grid points we confirm before launching: each point is a real engine process, so a
# big grid is real compute (and disk) the engineer should opt into deliberately.
_CONFIRM_ABOVE = 60

# Sweep dials with a SMALL set of discrete values -> tick-boxes (no free text, no misspelling):
# (engine param, [(checkbox x:Name, value string), ...]). A dial varies iff >=1 value is ticked. The
# value strings are typed by steelreuse_sweep.parse_values (unit-tested) -- floats, booleans, strings.
_CHECK_AXES = (
    ("objective", (("obj_co2", "co2"), ("obj_members", "members"),
                   ("obj_mass", "mass"), ("obj_balanced", "balanced"))),
    ("carbon_dataset", (("cb_ice_v3", "ice_v3"), ("cb_ice_v4", "ice_v4"),
                        ("cb_oekobaudat", "oekobaudat"))),
    ("counterfactual", (("cf_none", "none"), ("cf_recycling", "recycling"),
                        ("cf_rerolling", "rerolling"))),
    ("w_overspec", (("wo_0", "0.0"), ("wo_03", "0.3"))),
    ("splice", (("sp_off", "off"), ("sp_on", "on"))),
)
_CHECK_NAMES = tuple(name for _param, items in _CHECK_AXES for name, _val in items)

# Sweep dials with a LARGE range of values -> multi-select ListBox (param, listbox x:Name). The list
# is filled from the value lists below; the selected items are read back as the swept values.
_LIST_AXES = (
    ("min_util", "minutil_list"),
    ("max_distinct_sections", "maxsec_list"),
    ("knockdown", "knock_list"),
)
# 0.00, 0.05, ... 1.00 for the utilisation/knockdown lists; 'none' + 1..20 for the section cap.
_FRACTION_VALUES = ["%.2f" % (i * 0.05) for i in range(21)]
_MAXSEC_VALUES = ["none"] + [str(i) for i in range(1, 21)]
_LIST_VALUES = {"minutil_list": _FRACTION_VALUES, "knock_list": _FRACTION_VALUES,
                "maxsec_list": _MAXSEC_VALUES}


class SweepPlanner(forms.WPFWindow):
    """Reads the form into a sweep plan and runs it; opens the board when every point is done."""

    def __init__(self, ext_root):
        forms.WPFWindow.__init__(self, os.path.join(_DIR, "steelreuse_sweep_planner.xaml"))
        self._ext_root = ext_root
        self._settings = runner.load_settings(ext_root)

        donor = self._settings.get("last_donor")
        demand = self._settings.get("last_demand")
        if donor:
            self.donor_box.Text = donor
        if demand:
            self.demand_box.Text = demand if isinstance(demand, str) else "; ".join(demand)
        self.workers_box.Text = str(sweep.default_workers())
        # Fill the multi-select lists with their full value ranges (added as items, not ItemsSource,
        # so SelectedItems reads back the plain strings under IronPython).
        for lb_name, values in _LIST_VALUES.items():
            lb = getattr(self, lb_name)
            for v in values:
                lb.Items.Add(v)

        self.donor_browse.Click += self._pick_donor
        self.demand_browse.Click += self._pick_demand
        self.run_button.Click += self._on_run
        for name in _CHECK_NAMES:
            check = getattr(self, name)
            check.Checked += self._update_count
            check.Unchecked += self._update_count
        for _param, lb_name in _LIST_AXES:
            getattr(self, lb_name).SelectionChanged += self._update_count
        self._update_count()

    # -- inputs -----------------------------------------------------------------------------------
    def _axes(self):
        """The ordered ``(param, [values])`` axes from the ticked check-boxes and the selected list
        items; a dial with no selection is skipped. Values go through ``sweep.parse_values`` for
        typing (floats / booleans / 'none' -> None)."""
        axes = []
        for param, items in _CHECK_AXES:
            picked = [val for name, val in items if getattr(self, name).IsChecked]
            if picked:
                vals = sweep.parse_values(param, ", ".join(picked))
                if vals:
                    axes.append((param, vals))
        for param, lb_name in _LIST_AXES:
            picked = [str(item) for item in getattr(self, lb_name).SelectedItems]
            if picked:
                vals = sweep.parse_values(param, ", ".join(picked))
                if vals:
                    axes.append((param, vals))
        return axes

    def _update_count(self, sender=None, args=None):
        n = sweep.grid_size(self._axes())
        warn = "   (large -- you'll be asked to confirm)" if n > _CONFIRM_ABOVE else ""
        self.count_text.Content = "%d run(s)%s" % (n, warn)

    def _pick_donor(self, sender, args):
        path = buttons.pick_model_file("Donor (supply) model or inventory", owner=self)
        if path:
            self.donor_box.Text = path

    def _pick_demand(self, sender, args):
        picked = buttons.pick_model_file("New-design (demand) model or inventory",
                                         multi_file=True, owner=self)
        if picked:
            self.demand_box.Text = "; ".join(picked) if isinstance(picked, list) else picked

    # -- run --------------------------------------------------------------------------------------
    def _on_run(self, sender, args):
        donor = self.donor_box.Text.strip()
        demand = [p.strip() for p in self.demand_box.Text.split(";") if p.strip()]
        if not donor or not demand:
            forms.alert("Pick a donor and a demand model first (.json, .csv or .xlsx).",
                        title="SteelReuse")
            return
        axes = self._axes()
        if not axes:
            forms.alert("Enable at least one dial to vary (and give it values).", title="SteelReuse")
            return
        n = sweep.grid_size(axes)
        if n > _CONFIRM_ABOVE and not forms.alert(
                "This sweep is %d runs. Each is a separate engine process; it may take a while. "
                "Run it?" % n, yes=True, no=True, title="SteelReuse"):
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
        self._settings["last_donor"] = donor
        self._settings["last_demand"] = demand
        runner.save_settings(self._ext_root, self._settings)

        requested = self.workers_box.Text.strip()
        workers = sweep.clamp_workers(requested)
        out_root = os.path.join(runner.reports_dir(self._ext_root),
                                "sweep_" + time.strftime("%Y%m%d-%H%M%S"))
        # Realistic base shared by every point: moment-shape on (sharper, valid EN check) mirrors the
        # Run Match default. The National Annex is a single country for the WHOLE sweep (not a varied
        # dial) -- it sets the q_k imposed loads, the realistic-base side, so it belongs in the fixed
        # base. Donor/demand + the swept axes complete each point.
        fixed = {"donor": donor, "demand": demand, "moment_shape": True}
        na = self.na_combo.SelectedItem.Content if self.na_combo.SelectedItem else "en"
        if na:
            fixed["national_annex"] = na
        plan_rows = sweep.plan(fixed, axes, out_root)

        self.run_button.IsEnabled = False
        note = ""
        try:
            if int(requested) > workers:
                note = " (capped to your %d logical cores -- more gives no speed-up)" % sweep.cpu_total()
        except ValueError:
            pass
        self.progress_box.Text = ("Running %d match(es), %d at a time%s...\n"
                                  % (len(plan_rows), workers, note))
        threading.Thread(target=self._worker,
                         args=(interp, plan_rows, out_root, workers)).start()

    def _worker(self, interp, plan_rows, out_root, workers):
        """Off the UI thread: run the whole grid, then collect + mark the front and open the board."""
        def progress(done, total, row, result):
            mark = "ok" if result.get("ok") else "FAILED"
            self._ui(lambda: self._append("  [%d/%d] %s  %s" % (done, total, mark, row["label"])))
        try:
            sweep.run_grid(plan_rows, interp, max_workers=workers, on_done=progress)
        except Exception as ex:  # noqa: BLE001 -- surface any launch failure in the planner
            message = "Sweep failed to launch:\n" + str(ex)
            self._ui(lambda m=message: self._failed(m))
            return
        records = sweep.mark_front(sweep.collect(plan_rows))
        self._ui(lambda: self._done(records, out_root))

    def _ui(self, fn):
        self.Dispatcher.Invoke(Action(fn))

    def _append(self, line):
        self.progress_box.Text = self.progress_box.Text + line + "\n"

    def _failed(self, message):
        self.run_button.IsEnabled = True
        self._append(message)

    def _done(self, records, out_root):
        self.run_button.IsEnabled = True
        ok = sum(1 for r in records if r.get("ok"))
        front = sum(1 for r in records if r.get("on_front"))
        self._append("Done: %d run(s), %d ok, %d on the trade-off front. Opening the board..."
                     % (len(records), ok, front))
        board.BoardWindow(self._ext_root, records, out_root).show()
