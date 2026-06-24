# -*- coding: utf-8 -*-
"""SteelReuse donor-Review window: the extraction Problems + PDA QA, in Revit, with zoom-to-element.

A pyRevit ``forms.WPFWindow`` (default IronPython 3 engine -- stdlib + .NET only, no f-strings). This
is the in-Revit replacement for the browser report: two tabs (Problems, PDA QA) fed by the SAME
review.json (one engine run), each a grid whose rows select+zoom the element in the donor model -- a
browser table cannot do that. The printable HTML is still one click away (Open report).

It is a pure viewer: the engine run that produces review.json happens in the button preamble
(steelreuse_buttons.review_or_reuse) before the window opens. Parsing lives in the headless
:mod:`steelreuse_review_model`; document zoom goes through the shared :mod:`steelreuse_revit_events`.
"""

import os

import steelreuse_review_model as reviewmodel
import steelreuse_review_view as reviewview  # render the printable HTML report on demand
import steelreuse_revit_events as revit_events
import steelreuse_runner as runner
from pyrevit import forms

_DIR = os.path.dirname(__file__)


class ReviewWindow(forms.WPFWindow):
    """Two-tab donor review (Problems + PDA QA) over one review.json, with zoom-to-element."""

    def __init__(self, ext_root, review, tab="problems"):
        forms.WPFWindow.__init__(self, os.path.join(_DIR, "steelreuse_review_window.xaml"))
        self._ext_root = ext_root
        self._review = review

        self._zoom_handler = revit_events.ZoomHandler()
        self._zoom_event = revit_events.make_event(self._zoom_handler)
        self.problems_zoom.Click += self._on_zoom_problems
        self.pda_zoom.Click += self._on_zoom_pda
        self.problems_grid.MouseDoubleClick += self._on_zoom_problems
        self.pda_grid.MouseDoubleClick += self._on_zoom_pda
        self.open_report_button.Click += self._open_report
        self.open_folder_button.Click += self._open_folder

        self.problems_summary.Text = reviewmodel.problem_summary(review)
        self.pda_summary.Text = reviewmodel.pda_summary(review)
        self.problems_grid.ItemsSource = reviewmodel.problem_rows(review)
        self.pda_grid.ItemsSource = reviewmodel.pda_rows(review)
        if tab == "pda":
            self.tabs.SelectedItem = self.tab_pda

    # -- zoom (active donor model, via ExternalEvent) ---------------------------------------------
    def _zoom(self, grid):
        row = grid.SelectedItem
        if row is None:
            forms.alert("Select a row first (or double-click it).", title="SteelReuse")
            return
        if not row.id:
            return
        self._zoom_handler.ids = [row.id]
        self._zoom_event.Raise()

    def _on_zoom_problems(self, sender, args):
        self._zoom(self.problems_grid)

    def _on_zoom_pda(self, sender, args):
        self._zoom(self.pda_grid)

    # -- report actions ---------------------------------------------------------------------------
    def _reports_dir(self):
        out_dir = os.path.join(self._ext_root, "steelreuse_reports")
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        return out_dir

    def _open_report(self, sender, args):
        """Open the ACTIVE tab's printable HTML report in the browser (Problems vs PDA QA)."""
        out_dir = self._reports_dir()
        if self.tabs.SelectedItem is self.tab_pda:
            html = reviewview.render_pda_report(self._review)
            path, title = os.path.join(out_dir, "pda_report.html"), "SteelReuse pre-demolition audit"
        else:
            html = reviewview.render_problem_report(self._review)
            path, title = os.path.join(out_dir, "problems_report.html"), "SteelReuse extraction problems"
        try:
            runner.open_html_report(path, title, html)
        except Exception as ex:  # noqa: BLE001
            forms.alert("Could not open the report:\n" + str(ex), title="SteelReuse")

    def _open_folder(self, sender, args):
        os.startfile(self._reports_dir())
