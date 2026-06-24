# -*- coding: utf-8 -*-
"""Editable audit grid: load all donor members, edit inline / bulk-set, Save -> write_pda.

Default IronPython 3 engine, no f-strings. Runs a review to seed current values, drives the pure
steelreuse_audit_grid model, and commits via steelreuse_apply.write_pda. WPF shell modelled on
steelreuse_panel.py: the DataGrid binds to GridRow OBJECTS (plain attributes), with explicit columns
declared in the XAML -- WPF cannot auto-generate columns from Python dicts (it would show only the
dict's ``Count``), so the rows must expose real attributes.
"""

import os

import steelreuse_apply as apply
import steelreuse_audit_grid as gridmodel
import steelreuse_buttons as buttons
from pyrevit import forms, revit, script

_EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_XAML = os.path.join(_EXT_ROOT, "lib", "steelreuse_audit_grid.xaml")
output = script.get_output()


def _txt(value):
    return "" if value is None else str(value)


class GridRow:
    """One editable row as plain attributes so the WPF DataGrid can bind/edit it (dicts can't bind).

    Seeded from a steelreuse_audit_grid.build_rows dict; ``_orig`` keeps the seeded display strings so
    Save only writes the cells the user actually changed.
    """

    def __init__(self, m):
        self.id = _txt(m.get("id"))
        self.mark = _txt(m.get("mark"))
        self.section = _txt(m.get("section"))
        self.role = _txt(m.get("role"))
        for field in gridmodel.EDITABLE_FIELDS:
            setattr(self, field, _txt(m.get(field)))
        self._orig = dict((f, getattr(self, f)) for f in gridmodel.EDITABLE_FIELDS)


def _load_review():
    interp, donor = buttons.interpreter_and_donor(_EXT_ROOT)
    if not interp or not donor:
        return None
    review, err = buttons.review_or_reuse(_EXT_ROOT, interp, donor)
    if err is not None:
        detail = (err["stderr"] or err["stdout"] or "").strip()
        forms.alert("Review failed (exit %s):\n\n%s" % (err["returncode"], detail[-1500:]),
                    title="SteelReuse")
        return None
    return review


class AuditGridWindow(forms.WPFWindow):
    def __init__(self, review):
        forms.WPFWindow.__init__(self, _XAML)
        self._review = review
        self.rows = [GridRow(m) for m in gridmodel.build_rows(review)]
        self.dg.ItemsSource = self.rows
        self.bulkField.ItemsSource = list(gridmodel.EDITABLE_FIELDS)
        self.bulkApply.Click += self.on_bulk
        self.saveBtn.Click += self.on_save

    def on_bulk(self, sender, args):
        field = self.bulkField.SelectedItem
        if not field:
            return
        selected = list(self.dg.SelectedItems) or list(self.rows)
        for row in selected:
            setattr(row, field, self.bulkValue.Text)
        self.dg.Items.Refresh()

    def _payload(self):
        """Replay each row's CHANGED cells through the tested pure model -> {element_id: {field: val}}."""
        model_rows = gridmodel.build_rows(self._review)
        for i in range(len(self.rows)):     # index loop: IronPython 3 has no zip(strict=...)
            gr, mr = self.rows[i], model_rows[i]
            for field in gridmodel.EDITABLE_FIELDS:
                value = getattr(gr, field)
                if value != gr._orig.get(field):
                    gridmodel.set_value(mr, field, value)
        return gridmodel.write_payload(model_rows)

    def on_save(self, sender, args):
        payload = self._payload()
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
        self.status.Text = "saved %d row(s)" % written


def main():
    review = _load_review()
    if not review:
        return
    AuditGridWindow(review).ShowDialog()


if __name__ == "__main__":
    main()
