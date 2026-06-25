# -*- coding: utf-8 -*-
"""SteelReuse Scenario Sweep board: the ranked trade-off table for a finished sweep.

A pyRevit ``forms.WPFWindow`` (default IronPython 3 engine -- stdlib + .NET only, no f-strings). Pure
view over the records :func:`steelreuse_sweep.collect` / :func:`steelreuse_sweep.mark_front` produced;
no engine runs here. Rows on the non-dominated trade-off **front** are highlighted (the genuine
trade-offs -- you can't improve one currency without giving up another); failed points are greyed.
Each point is a normal ``results.json`` run, so "Open folder" reaches its full output (and the
Compare / Results windows can open it too).
"""

import os

from pyrevit import forms

_DIR = os.path.dirname(__file__)


def _fmt(value, decimals=0):
    """Display a number, '-' for None; ``decimals`` controls float precision (0 = integer)."""
    if value is None:
        return "-"
    if decimals:
        return "%.*f" % (decimals, value)
    return str(value)


class _Row:
    """One board row (plain attributes so WPF binds them directly)."""

    __slots__ = ("label", "objective", "reused", "co2", "mass", "distinct", "unfilled",
                 "reuse_rate", "status", "front_mark", "front_flag", "ok_flag", "out_dir")

    def __init__(self, rec):
        self.label = rec.get("label", "")
        self.objective = rec.get("objective", "")
        self.reused = _fmt(rec.get("reused"))
        self.co2 = _fmt(rec.get("co2_saved_kg"), 1)
        self.mass = _fmt(rec.get("mass_reused_kg"), 1)
        self.distinct = _fmt(rec.get("distinct_sections"))
        self.unfilled = _fmt(rec.get("unfilled"))
        rate = rec.get("reuse_rate_pct")
        self.reuse_rate = (_fmt(rate, 1) + "%") if rate is not None else "-"
        if not rec.get("ok"):
            self.status = "failed"
        elif rec.get("proven_optimal"):
            self.status = "optimal"
        else:
            self.status = rec.get("solver_status", "") or "heuristic"
        self.front_mark = "*" if rec.get("on_front") else ""
        self.front_flag = "front" if rec.get("on_front") else ""
        self.ok_flag = "ok" if rec.get("ok") else "fail"
        self.out_dir = rec.get("out_dir", "")


def _co2_key(rec):
    """Sort key helper: CO2 saved with missing pushed to the bottom."""
    value = rec.get("co2_saved_kg")
    return value if value is not None else float("-inf")


class BoardWindow(forms.WPFWindow):
    """Shows the sweep's records front-first; opens a point's run folder on demand."""

    def __init__(self, ext_root, records, out_root):
        forms.WPFWindow.__init__(self, os.path.join(_DIR, "steelreuse_sweep_board.xaml"))
        self._ext_root = ext_root
        self._out_root = out_root

        self.open_button.Click += self._on_open
        self.open_root_button.Click += self._on_open_root
        self.grid.MouseDoubleClick += self._on_open

        # Front first, then most CO2 saved: a sensible default order even though the front leads and
        # every column stays user-sortable.
        ordered = sorted(records, key=lambda r: (not r.get("on_front"), -_co2_key(r)))
        self.grid.ItemsSource = [_Row(r) for r in ordered]

        ok = sum(1 for r in records if r.get("ok"))
        front = sum(1 for r in records if r.get("on_front"))
        self.summary_text.Text = (
            "%d run(s), %d ok, %d on the trade-off front (highlighted). A front row is one no other "
            "run beats on every currency at once. Double-click a row to open its run folder."
            % (len(records), ok, front))

    def _selected_dir(self):
        row = self.grid.SelectedItem
        if row is None:
            forms.alert("Select a row first.", title="SteelReuse")
            return None
        return row.out_dir

    def _on_open(self, sender, args):
        out_dir = self._selected_dir()
        if out_dir is None:
            return
        if out_dir and os.path.isdir(out_dir):
            os.startfile(out_dir)
        else:
            forms.alert("That run produced no output folder.", title="SteelReuse")

    def _on_open_root(self, sender, args):
        if self._out_root and os.path.isdir(self._out_root):
            os.startfile(self._out_root)
        else:
            forms.alert("The sweep output folder is not available.", title="SteelReuse")
