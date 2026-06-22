# -*- coding: utf-8 -*-
"""Editable audit grid: load all donor members, edit inline / bulk-set, Save -> write_pda.

Default IronPython 3 engine, no f-strings. Runs a review to seed current values, drives the pure
steelreuse_audit_grid model, and commits via steelreuse_apply.write_pda. WPF shell modelled on
steelreuse_compare.py.
"""

import json
import os

import steelreuse_apply as apply
import steelreuse_audit_grid as gridmodel
import steelreuse_runner as runner
from pyrevit import forms, revit, script

_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_XAML = os.path.join(_EXT_ROOT, "lib", "steelreuse_audit_grid.xaml")
output = script.get_output()


def _load_review():
    interp = runner.discover_interpreter(runner.load_settings(_EXT_ROOT).get("interpreter"), _EXT_ROOT)
    donor = runner.load_settings(_EXT_ROOT).get("last_donor")
    if not (donor and os.path.isfile(donor)):
        donor = forms.pick_file(file_ext="json", title="Select the extracted donor.json")
    if not interp or not donor:
        return None
    out_dir = os.path.join(_EXT_ROOT, "steelreuse_reports")
    res = runner.run_review(interp, {"donor": donor}, out_dir)
    if not res["ok"]:
        forms.alert("Review failed:\n\n%s" % (res["stderr"] or res["stdout"]), title="SteelReuse")
        return None
    with open(res["paths"]["review_json"], encoding="utf-8") as handle:
        return json.load(handle)


class AuditGridWindow(forms.WPFWindow):
    def __init__(self, rows):
        forms.WPFWindow.__init__(self, _XAML)
        self.rows = rows
        self.dg.ItemsSource = rows
        self.bulkField.ItemsSource = list(gridmodel.EDITABLE_FIELDS)
        self.bulkApply.Click += self.on_bulk
        self.saveBtn.Click += self.on_save

    def on_bulk(self, sender, args):
        field = self.bulkField.SelectedItem
        if not field:
            return
        selected = list(self.dg.SelectedItems) or self.rows
        gridmodel.bulk_set(selected, field, self.bulkValue.Text)
        self.dg.Items.Refresh()

    def on_save(self, sender, args):
        payload = gridmodel.write_payload(self.rows)
        if not payload:
            self.status.Text = "nothing changed"
            return
        doc = revit.doc
        written = 0
        for eid_str, values in payload.items():
            elem = doc.GetElement(eid_str)        # try UniqueId
            eid = elem.Id if elem is not None else None
            if eid is None:
                try:
                    eid = revit.DB.ElementId(int(eid_str))
                except Exception:
                    continue
            written += apply.write_pda(doc, [eid], values)["written"]
        self.status.Text = "saved %d" % written


def main():
    review = _load_review()
    if not review:
        return
    rows = gridmodel.build_rows(review)
    AuditGridWindow(rows).ShowDialog()


if __name__ == "__main__":
    main()
