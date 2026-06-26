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

# The dials this planner exposes as sweep axes. Each value is picked from tick-boxes (no free text ->
# no misspelling): (engine param, [(checkbox x:Name, value string), ...]). A dial varies only when at
# least one of its values is ticked. The value strings are typed by steelreuse_sweep.parse_values
# (unit-tested), keyed by the param name -- 'none' -> None for the section cap, floats for the rest.
_AXES = (
    ("objective", (("obj_co2", "co2"), ("obj_members", "members"),
                   ("obj_mass", "mass"), ("obj_balanced", "balanced"))),
    ("min_util", (("mu_00", "0.0"), ("mu_05", "0.5"), ("mu_06", "0.6"), ("mu_07", "0.7"))),
    ("max_distinct_sections", (("ms_none", "none"), ("ms_6", "6"), ("ms_8", "8"), ("ms_10", "10"))),
    ("knockdown", (("kd_10", "1.0"), ("kd_09", "0.9"), ("kd_085", "0.85"), ("kd_08", "0.8"))),
    ("carbon_dataset", (("cb_ice_v3", "ice_v3"), ("cb_ice_v4", "ice_v4"),
                        ("cb_oekobaudat", "oekobaudat"))),
)
# Flat list of every value checkbox, for wiring the live run-count update.
_AXIS_CHECKS = tuple(name for _param, items in _AXES for name, _val in items)


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

        self.donor_browse.Click += self._pick_donor
        self.demand_browse.Click += self._pick_demand
        self.run_button.Click += self._on_run
        for name in _AXIS_CHECKS:
            check = getattr(self, name)
            check.Checked += self._update_count
            check.Unchecked += self._update_count
        self._update_count()

    # -- inputs -----------------------------------------------------------------------------------
    def _axes(self):
        """The ordered ``(param, [values])`` axes from the ticked values; a dial with no ticks is
        skipped. Ticked value strings go through ``sweep.parse_values`` for typing (floats / None)."""
        axes = []
        for param, items in _AXES:
            picked = [val for name, val in items if getattr(self, name).IsChecked]
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
        # Run Match default. Donor/demand + the swept axes complete each point.
        fixed = {"donor": donor, "demand": demand, "moment_shape": True}
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
